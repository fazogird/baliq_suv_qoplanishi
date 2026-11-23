"""Baliq hovuzlari: Sentinel-2 NDWI bo'yicha suv bilan qoplangan maydon foizi.

Har hovuz (polygon) × har sana (boshi..oxiri oralig'idagi Sentinel-2 L2A sahnalari) uchun:
  NDWI = (Yashil - NIR) / (Yashil + NIR)          (McFeeters, 1996; B03, B08 — 10 m)
  piksel suv  <=>  NDWI > chegara (standart 0.0)
  suv_foiz = suv piksellar / toza piksellar * 100   (toza = SCL bo'yicha bulut/soya emas)

Qaysi piksellar hovuzniki: markazi polygon ichida bo'lganlar (all_touched=False).

Natija — keng jadval: har hovuz bitta qator, har sana alohida ustun (nomi YYYYMMDD, masalan 20260301):
  <chiqish>.shp       — ustunda o'sha kungi suv bilan qoplangan maydon foizi (0..100)
  <chiqish>_ndwi.shp  — xuddi shu ustunlar, ichida o'sha kungi hovuz NDWI o'rtachasi
Bulutli kun (toza ulush < --min-toza) yoki sahna hovuzni qamramagan kun — katak bo'sh.

Ishga tushirish (repo papkasidan):
    .\\run.bat ndwi_hovuz.py --hovuz D:\\...\\hovuzlar.shp --boshi 2026-04-01 --oxiri 2026-10-31
    .\\run.bat ndwi_hovuz.py --hovuz hovuzlar.shp --boshi 2026-06-01 --oxiri 2026-06-30 --chiqish D:\\natija\\iyun.shp
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _conda_yollari() -> None:
    """Conda muhiti 'activate' qilinmasa ham ishlasin: Library\\bin, GDAL_DATA, PROJ_LIB (numpy dan OLDIN).

    Aks holda Windows boshqa joydagi mos kelmaydigan DLL ni yuklab, rasterio/numpy `0xc06d007f` bilan qulaydi.
    """
    p = sys.prefix
    dirs = [p, os.path.join(p, "Library", "mingw-w64", "bin"), os.path.join(p, "Library", "usr", "bin"),
            os.path.join(p, "Library", "bin"), os.path.join(p, "Scripts"), os.path.join(p, "bin")]
    os.environ["PATH"] = os.pathsep.join([d for d in dirs if os.path.isdir(d)] + [os.environ.get("PATH", "")])
    for d in dirs:
        if os.path.isdir(d) and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
    for var, sub in (("GDAL_DATA", "gdal"), ("PROJ_LIB", "proj"), ("PROJ_DATA", "proj")):
        d = os.path.join(p, "Library", "share", sub)
        if os.path.isdir(d):
            os.environ[var] = d
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


_conda_yollari()

import argparse  # noqa: E402
import collections  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyogrio  # noqa: E402

MANBALAR = {
    "aws": dict(stac="https://earth-search.aws.element84.com/v1", collection="sentinel-2-c1-l2a",
                kanallar={"yashil": "green", "nir": "nir", "scl": "scl"}, imzo=False),
    "pc": dict(stac="https://planetarycomputer.microsoft.com/api/stac/v1", collection="sentinel-2-l2a",
               kanallar={"yashil": "B03", "nir": "B08", "scl": "SCL"}, imzo=True),
}
# SCL: 2 qorong'i joy, 4 o'simlik, 5 ochiq tuproq, 6 suv, 7 aniqlanmagan. 3 (bulut soyasi), 8-10 (bulut), 11 (qor) — yo'q.
# 2 ham olinadi: chuqur/loyqa suvni Sen2Cor ba'zan "qorong'i joy" deb belgilaydi.
SCL_TOZA = [2, 4, 5, 6, 7]
REFL_MAX = 1.5
MIN_MAXRAJ = 0.01
GURUH_GRAD = 0.2          # hovuzlar ~20 km li kataklarga guruhlanadi (har guruh o'z kichik to'rida o'qiladi)
PIKSEL_M = 10

# tarmoq osilib qolmasin: o'qish 60 s dan oshsa qayta urinish, so'ng xato (kun o'tkazib yuboriladi)
for _k, _v in {"GDAL_HTTP_TIMEOUT": "60", "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2"}.items():
    os.environ.setdefault(_k, _v)
warnings.filterwarnings("ignore", message="Could not parse bucket")          # pystac storage kengaytmasi
warnings.filterwarnings("ignore", category=UserWarning, module="pystac")
warnings.filterwarnings("ignore", message="Dataset has no geotransform")      # sahna to'rni qamramagan kun


def log(m: str) -> None:
    print(m, flush=True)


# ---------------------------------------------------------------- hisob (sof numpy, test qilinadi)
def ndwi_hisob(yashil: np.ndarray, nir: np.ndarray, scl: np.ndarray, scl_toza=SCL_TOZA):
    """-> (toza_niqob, ndwi). Qaytish birligida (0..1). Suvda NIR ~0 yoki ozgina manfiy bo'lishi mumkin."""
    ok = np.isin(scl, scl_toza)
    ok &= np.isfinite(yashil) & np.isfinite(nir)
    ok &= (yashil > 0) & (yashil <= REFL_MAX) & (nir > -0.05) & (nir <= REFL_MAX)
    s = yashil + nir
    ok &= s > MIN_MAXRAJ
    with np.errstate(divide="ignore", invalid="ignore"):
        ndwi = np.clip((yashil - nir) / s, -1, 1).astype("float32")
    ok &= np.isfinite(ndwi)
    return ok, ndwi


def hovuz_statistika(labs, qamrov, ok, ndwi, n, chegara):
    """Yorliq rastrlari bo'yicha har hovuz uchun: qamralgan, toza, suv piksellar soni va NDWI yig'indisi."""
    qam = np.zeros(n + 1); toza = np.zeros(n + 1); suv = np.zeros(n + 1); yig = np.zeros(n + 1)
    for lab in labs:
        ic = lab > 0
        qam += np.bincount(lab[ic & qamrov], minlength=n + 1)
        sel = ic & ok
        l = lab[sel]; v = ndwi[sel].astype("float64")
        toza += np.bincount(l, minlength=n + 1)
        suv += np.bincount(l, weights=(v > chegara).astype("float64"), minlength=n + 1)
        yig += np.bincount(l, weights=v, minlength=n + 1)
    return qam[1:], toza[1:], suv[1:], yig[1:]


# ---------------------------------------------------------------- geometriya
def qatlamlar(geoms) -> np.ndarray:
    """Ustma-ust tushgan hovuzlarni turli qatlamga ajratish (bitta yorliq rastrida bir piksel = bitta hovuz)."""
    gs = gpd.GeoSeries(geoms)
    sidx = gs.sindex
    q = np.full(len(gs), -1)
    for i, g in enumerate(gs):
        band = {q[j] for j in sidx.query(g, predicate="intersects")
                if j != i and q[j] >= 0 and g.intersection(gs.iloc[j]).area > 0}
        L = 0
        while L in band:
            L += 1
        q[i] = L
    return q


def yorliqlar(geoms, qatlam, gb):
    from rasterio.features import rasterize
    labs = []
    for L in sorted(set(qatlam)):
        idx = np.where(qatlam == L)[0]
        labs.append(rasterize(((geoms[i], int(i) + 1) for i in idx), out_shape=gb.shape, transform=gb.affine,
                              fill=0, dtype="int32"))
    return labs


# ---------------------------------------------------------------- Sentinel-2
def sahnalar(m, bbox, boshi, oxiri, bulut_max):
    from pystac_client import Client
    mod = None
    if m["imzo"]:
        import planetary_computer
        mod = planetary_computer.sign_inplace
    cl = Client.open(m["stac"], modifier=mod)
    return cl.search(collections=[m["collection"]], bbox=bbox, datetime=f"{boshi}/{oxiri}",
                     query={"eo:cloud_cover": {"lt": bulut_max}}).item_collection()


def masshtab(item, asset):
    rb = item.assets[asset].extra_fields.get("raster:bands", [{}])[0]
    sc, of = rb.get("scale"), rb.get("offset")
    if sc is None:                       # Planetary Computer: raster:bands yo'q; baseline >= 04.00 da DN +1000
        sc = 1e-4
        of = -0.1 if str(item.properties.get("s2:processing_baseline", "00.00")) >= "04.00" else 0.0
    return sc, of or 0.0


def bir_kun(items, gb, kan, labs, n, chegara, scl_toza):
    import odc.stac
    ds = odc.stac.load(items, bands=list(kan.values()), geobox=gb, groupby="solar_day", resampling="nearest",
                       chunks=None, fail_on_error=False)

    def r(nom):
        sc, of = masshtab(items[0], kan[nom])
        a = ds[kan[nom]].values[0]
        return np.where(a == 0, np.nan, a.astype("float32") * sc + of)

    scl = ds[kan["scl"]].values[0]
    ok, ndwi = ndwi_hisob(r("yashil"), r("nir"), scl, scl_toza)
    return hovuz_statistika(labs, scl > 0, ok, ndwi, n, chegara)


# ---------------------------------------------------------------- yozish
SHP_MAX_USTUN = 254


def yoz(suv: gpd.GeoDataFrame, ndwi: gpd.GeoDataFrame, chiqish: Path) -> list[Path]:
    """suv -> chiqish; ndwi -> <chiqish>_ndwi (gpkg/xlsx da bitta faylda ikki qatlam/varaq)."""
    chiqish.parent.mkdir(parents=True, exist_ok=True)
    ext = chiqish.suffix.lower()
    jadval = lambda g: pd.DataFrame(g.drop(columns="geometry"))  # noqa: E731
    n_yol = chiqish.with_name(chiqish.stem + "_ndwi" + chiqish.suffix)
    if ext == ".shp":
        if suv.shape[1] - 1 > SHP_MAX_USTUN:
            raise SystemExit(f"shp da {SHP_MAX_USTUN} tadan ortiq ustun bo'lmaydi ({suv.shape[1] - 1} ta). "
                             f"Davrni qisqartiring yoki --chiqish ni .gpkg qiling.")
        pyogrio.write_dataframe(suv, chiqish, encoding="UTF-8")
        pyogrio.write_dataframe(ndwi, n_yol, encoding="UTF-8")
        return [chiqish, n_yol]
    if ext == ".gpkg":
        if chiqish.exists():
            chiqish.unlink()
        pyogrio.write_dataframe(suv, chiqish, layer="suv_foiz")
        pyogrio.write_dataframe(ndwi, chiqish, layer="ndwi", append=True)
        return [chiqish]
    if ext == ".csv":
        jadval(suv).to_csv(chiqish, index=False, encoding="utf-8-sig")
        jadval(ndwi).to_csv(n_yol, index=False, encoding="utf-8-sig")
        return [chiqish, n_yol]
    if ext == ".xlsx":
        with pd.ExcelWriter(chiqish) as w:
            jadval(suv).to_excel(w, sheet_name="suv_foiz", index=False)
            jadval(ndwi).to_excel(w, sheet_name="ndwi", index=False)
        return [chiqish]
    raise SystemExit(f"Chiqish turi qo'llanmaydi: {ext} (.shp, .gpkg, .csv, .xlsx)")


# ---------------------------------------------------------------- asosiy
def main() -> None:
    ap = argparse.ArgumentParser(description="Baliq hovuzlari: Sentinel-2 NDWI va suv bilan qoplangan maydon foizi")
    ap.add_argument("--hovuz", required=True, help="hovuz polygonlari (.shp, .gpkg, .geojson ...)")
    ap.add_argument("--boshi", required=True, help="boshlanish sanasi, YYYY-MM-DD")
    ap.add_argument("--oxiri", required=True, help="tugash sanasi, YYYY-MM-DD (kiradi)")
    ap.add_argument("--chiqish", help="natija fayli: .shp (standart) | .gpkg | .csv | .xlsx")
    ap.add_argument("--qatlam", help="gpkg ichidagi qatlam nomi")
    ap.add_argument("--id", dest="id_ustun", help="hovuz ID ustuni (standart: id/uniqueid bo'lsa o'sha, aks holda 1..n)")
    ap.add_argument("--chegara", type=float, default=0.0, help="NDWI > chegara => suv (standart 0.0)")
    ap.add_argument("--min-toza", type=float, default=0.6,
                    help="hovuzning kamida shuncha ulushi toza bo'lsa kun hisoblanadi (standart 0.6)")
    ap.add_argument("--bulut-max", type=float, default=80, help="sahna buluti %% dan kam sahnalar (standart 80)")
    ap.add_argument("--scl", default=",".join(map(str, SCL_TOZA)), help=f"toza SCL sinflari (standart {SCL_TOZA})")
    ap.add_argument("--manba", choices=list(MANBALAR), default="aws")
    ap.add_argument("--ishchi", type=int, default=6)
    a = ap.parse_args()

    boshi, oxiri = pd.Timestamp(a.boshi).date(), pd.Timestamp(a.oxiri).date()
    if boshi > oxiri:
        raise SystemExit("--boshi --oxiri dan keyin")
    kirish = Path(a.hovuz)
    chiqish = Path(a.chiqish) if a.chiqish else kirish.with_name(
        f"{kirish.stem}_suv_{boshi:%Y%m%d}_{oxiri:%Y%m%d}.shp")
    scl_toza = [int(x) for x in a.scl.split(",")]
    m = MANBALAR[a.manba]
    t0 = time.time()

    # --- hovuzlar
    H = pyogrio.read_dataframe(kirish, layer=a.qatlam)
    if H.crs is None:
        raise SystemExit("Polygon faylida CRS yo'q (.prj)")
    H = H[H.geometry.notna() & ~H.geometry.is_empty].reset_index(drop=True)
    H["geometry"] = H.geometry.make_valid()
    H = H[H.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    idc = a.id_ustun or next((c for c in ("id", "uniqueid", "unique_id", "hovuz_id") if c in H.columns), None)
    if idc is None:
        idc = "hovuz_id"
        H.insert(0, idc, np.arange(1, len(H) + 1))
    if idc not in H.columns:
        raise SystemExit(f"ID ustuni yo'q: {idc}. Bor ustunlar: {list(H.columns)}")
    if H[idc].duplicated().any():
        raise SystemExit(f"ID ustunida takror qiymatlar bor: {idc}")
    n = len(H)
    g84 = H.to_crs(4326)
    c = g84.geometry.representative_point()
    guruh = (np.floor(c.x / GURUH_GRAD).astype(int).astype(str) + "_" + np.floor(c.y / GURUH_GRAD).astype(int).astype(str)).values
    H["maydon_ga"] = np.nan
    log(f"{kirish.name}: {n} hovuz, {len(set(guruh))} guruh | {boshi}..{oxiri} | NDWI > {a.chegara} = suv, "
        f"toza ulush >= {a.min_toza}")

    # --- har guruh: to'r, yorliqlar, sahnalar
    import odc.stac
    from odc.geo.geobox import GeoBox
    if a.manba == "aws":
        odc.stac.configure_rio(cloud_defaults=True, aws={"aws_unsigned": True})
    else:
        odc.stac.configure_rio(cloud_defaults=True)
    vazifalar, jami_px = [], np.zeros(n)
    for gk in sorted(set(guruh)):
        idx = np.where(guruh == gk)[0]
        gg = H.iloc[idx]
        crs = gg.estimate_utm_crs()
        gu = gg.to_crs(crs)
        H.loc[idx, "maydon_ga"] = gu.area.values / 1e4
        xmin, ymin, xmax, ymax = gu.total_bounds
        gb = GeoBox.from_bbox((xmin - 20, ymin - 20, xmax + 20, ymax + 20), crs=crs, resolution=PIKSEL_M)
        geoms = list(gu.geometry.values)
        labs = yorliqlar(geoms, qatlamlar(geoms), gb)
        for lab in labs:
            jami_px[idx] += np.bincount(lab.ravel(), minlength=len(idx) + 1)[1:]
        ic = sahnalar(m, list(g84.iloc[idx].total_bounds), boshi, oxiri, a.bulut_max)
        kunlar = collections.defaultdict(list)
        for it in ic:
            kunlar[it.datetime.date()].append(it)
        log(f"  guruh {gk}: {len(idx)} hovuz, to'r {gb.shape[1]}x{gb.shape[0]}, {len(ic)} sahna, {len(kunlar)} kun")
        vazifalar += [(idx, gb, labs, d, its) for d, its in kunlar.items()]
    kichik = (jami_px == 0).sum()
    if kichik:
        log(f"  DIQQAT: {kichik} hovuzda birorta 10 m piksel markazi yo'q (juda kichik) — natija bo'sh qoladi")

    # --- hisob
    qatorlar = []
    with ThreadPoolExecutor(a.ishchi) as ex:
        futs = {ex.submit(bir_kun, its, gb, m["kanallar"], labs, len(idx), a.chegara, scl_toza): (idx, d)
                for idx, gb, labs, d, its in vazifalar}
        for j, f in enumerate(as_completed(futs), 1):
            idx, d = futs[f]
            try:
                qam, toza, suv, yig = f.result()
            except Exception as e:  # noqa: BLE001
                log(f"  {d}: XATO {type(e).__name__}: {str(e)[:150]}")
                continue
            for k in np.where(qam > 0)[0]:
                qatorlar.append((idx[k], d, qam[k], toza[k], suv[k], yig[k]))
            if j % 10 == 0 or j == len(futs):
                log(f"  {j}/{len(futs)} kun | {time.time() - t0:.0f} s")

    q = pd.DataFrame(qatorlar, columns=["i", "sana", "qam", "toza", "suv", "yig"])
    jp = jami_px[q.i.values] if len(q) else np.zeros(0)
    yaxshi = (q.toza / np.where(jp > 0, jp, np.nan) >= a.min_toza) & (q.qam >= 0.99 * jp)
    q = q[yaxshi.values].copy()
    q["ndwi"] = (q.yig / q.toza).round(3)
    q["suv_foiz"] = (100 * q.suv / q.toza).round(1)
    q["ustun"] = pd.to_datetime(q.sana).dt.strftime("%Y%m%d")
    sanalar = sorted(q.ustun.unique())

    # --- keng jadval: har hovuz bitta qator, har sana alohida ustun (faqat kamida bitta hovuzda toza kunlar)
    def keng(qiymat: str) -> gpd.GeoDataFrame:
        p = q.pivot(index="i", columns="ustun", values=qiymat).reindex(index=range(n), columns=sanalar)
        df = pd.DataFrame({idc: H[idc].values, "maydon_ga": H.maydon_ga.round(4).values,
                           "piksel": jami_px.astype(int)})
        df = pd.concat([df, p.reset_index(drop=True)], axis=1)
        return gpd.GeoDataFrame(df, geometry=H.geometry.values, crs=H.crs)

    suv_j, ndwi_j = keng("suv_foiz"), keng("ndwi")
    fayllar = yoz(suv_j, ndwi_j, chiqish)
    bor = suv_j[sanalar].notna().sum(axis=1)
    log(f"Tayyor: {n} hovuz × {len(sanalar)} sana ustuni ({sanalar[0] if sanalar else '-'} .. "
        f"{sanalar[-1] if sanalar else '-'}); hovuz boshiga toza kun: o'rtacha {bor.mean():.1f}, "
        f"kuzatuvsiz hovuz {int((bor == 0).sum())} | {time.time() - t0:.0f} s")
    for f in fayllar:
        log(f"  -> {f}")


if __name__ == "__main__":
    main()
