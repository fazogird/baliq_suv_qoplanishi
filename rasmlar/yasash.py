"""README rasmlarini yasash: ndwi_hovuz.py natijasi va o'sha joyning Sentinel-2 tasviridan.

    .\\run.bat rasmlar\\yasash.py --hovuz hovuzlar.gpkg --natija natija.gpkg [--sana 2026-06-19]

--natija — ndwi_hovuz.py ning .gpkg chiqishi (suv_foiz va ndwi qatlamlari).
Rasmlarda koordinata va hovuz ID lari ko'rsatilmaydi (hovuzlar harf bilan belgilanadi).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ndwi_hovuz as N  # noqa: E402  (conda yo'llari shu yerda sozlanadi)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyogrio  # noqa: E402

SUV, QURUQ, BULUT, CHIZIQ = "#2b7bba", "#d9c7a0", "#c4c4c4", "#d62728"
plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
CHIQ = Path(__file__).resolve().parent


def saqla(fig, nom):
    fig.savefig(CHIQ / nom, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  ->", CHIQ / nom)


def sana_ustunlar(df):
    return [c for c in df.columns if c.isdigit() and len(c) == 8]


# ------------------------------------------------------------------ 1. hisob sxemasi (sun'iy)
def rasm_sxema():
    rng = np.random.default_rng(3)
    n = 8
    yy, xx = np.mgrid[:n, :n]
    suv = (xx + 0.6 * yy) < 7.5                                   # hovuzning chap qismi suv
    holatlar = [("Ochiq kun", np.zeros((n, n), bool)),
                ("Qisman bulut", (xx >= 5) & (yy <= 2)),
                ("Ko'p bulut", (yy <= 4) | (xx >= 6))]
    fig, axs = plt.subplots(1, 3, figsize=(13, 5.2))
    for ax, (nom, bulut) in zip(axs, holatlar):
        img = np.where(bulut, 2, np.where(suv, 0, 1))
        ax.imshow(img, cmap=ListedColormap([SUV, QURUQ, BULUT]), vmin=0, vmax=2)
        for i in range(n + 1):
            ax.axhline(i - .5, color="white", lw=1); ax.axvline(i - .5, color="white", lw=1)
        ax.add_patch(Rectangle((-.5, -.5), n, n, fill=False, ec="black", lw=2.5))
        jami, toza = n * n, int((~bulut).sum()); s = int((suv & ~bulut).sum())
        ok = toza / jami >= 0.6
        natija = f"suv = {s} / {toza} = {100 * s / toza:.0f}%" if ok else "katak BO'SH qoladi"
        ax.set_title(nom, fontsize=13, fontweight="bold")
        ax.set_xlabel(f"toza: {toza}/{jami} = {100 * toza / jami:.0f}%  " + ("≥ 60% ✓" if ok else "< 60% ✗")
                      + f"\n{natija}", fontsize=12, color="black" if ok else CHIZIQ)
        ax.set_xticks([]); ax.set_yticks([])
    fig.legend(handles=[Patch(color=SUV, label="suv (NDWI > 0)"), Patch(color=QURUQ, label="suv emas (NDWI ≤ 0)"),
                        Patch(color=BULUT, label="bulut / soya (hisobga olinmaydi)")],
               loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(.5, -.06))
    fig.suptitle("Bitta hovuz, 8×8 = 64 ta 10 m piksel: suv foizi faqat bulutsiz piksellardan", fontsize=13)
    saqla(fig, "02_hisob_sxemasi.png")


# ------------------------------------------------------------------ Sentinel-2 bo'lagi
def s2_bolak(hovuz, sana):
    import odc.stac
    from odc.geo.geobox import GeoBox
    odc.stac.configure_rio(cloud_defaults=True, aws={"aws_unsigned": True})
    m = N.MANBALAR["aws"]
    u = hovuz.to_crs(hovuz.estimate_utm_crs())
    xmin, ymin, xmax, ymax = u.total_bounds
    gb = GeoBox.from_bbox((xmin - 150, ymin - 150, xmax + 150, ymax + 150), crs=u.crs, resolution=10)
    ic = N.sahnalar(m, list(hovuz.to_crs(4326).total_bounds), sana, sana, 100)
    items = list(ic)
    ds = odc.stac.load(items, bands=["red", "green", "blue", "nir", "scl"], geobox=gb, groupby="solar_day",
                       chunks=None, resampling="nearest")
    r = {}
    for b in ["red", "green", "blue", "nir"]:
        sc, of = N.masshtab(items[0], b)
        a = ds[b].values[0]
        r[b] = np.where(a == 0, np.nan, a.astype("float32") * sc + of)
    scl = ds["scl"].values[0]
    ok, ndwi = N.ndwi_hisob(r["green"], r["nir"], scl)
    rgb = np.clip(np.dstack([r["red"], r["green"], r["blue"]]) / 0.25, 0, 1)
    lab = N.yorliqlar(list(u.geometry.values), np.zeros(len(u), int), gb)[0]
    ext = (gb.extent.boundingbox.left, gb.extent.boundingbox.right,
           gb.extent.boundingbox.bottom, gb.extent.boundingbox.top)
    return u, rgb, ndwi, ok, lab, ext


def chegaralar(ax, u, rang="black", lw=1.2):
    u.boundary.plot(ax=ax, color=rang, lw=lw)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")


# ------------------------------------------------------------------ 2. usul: RGB -> NDWI -> suv niqobi
def rasm_usul(u, rgb, ndwi, ok, lab, ext, suv_qator, sana):
    fig, axs = plt.subplots(1, 3, figsize=(16, 7))
    axs[0].imshow(rgb, extent=ext); chegaralar(axs[0], u, "yellow", 1.4)
    axs[0].set_title("1. Sentinel-2 tasviri (haqiqiy rang)\nsariq — hovuz chegaralari (polygon)")
    im = axs[1].imshow(np.where(ok, ndwi, np.nan), extent=ext, cmap="BrBG", vmin=-0.6, vmax=0.6)
    chegaralar(axs[1], u)
    axs[1].set_title("2. NDWI = (Yashil − NIR) / (Yashil + NIR)\nyashil-ko'k — suv, jigarrang — suv emas")
    fig.colorbar(im, cax=axs[1].inset_axes([0.1, -0.09, 0.8, 0.04]), orientation="horizontal", label="NDWI")
    niq = np.full(ndwi.shape, np.nan)
    ich = lab > 0
    niq[ich & ok] = np.where(ndwi[ich & ok] > 0, 0, 1)
    niq[ich & ~ok] = 2
    axs[2].imshow(rgb * 0.35 + 0.65, extent=ext)
    axs[2].imshow(niq, extent=ext, cmap=ListedColormap([SUV, QURUQ, BULUT]), vmin=0, vmax=2, interpolation="nearest")
    chegaralar(axs[2], u)
    for (_, g), v in zip(u.iterrows(), suv_qator):
        if g.geometry.area >= 4.5e4 and np.isfinite(v):
            p = g.geometry.representative_point()
            axs[2].text(p.x, p.y, f"{v:.0f}%", ha="center", va="center", fontsize=9, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    axs[2].set_title("3. NDWI > 0 — suv; har hovuzda\nsuv piksellar / hovuz piksellari = suv foizi")
    axs[2].legend(handles=[Patch(color=SUV, label="suv"), Patch(color=QURUQ, label="suv emas")],
                  loc="upper center", bbox_to_anchor=(.5, -0.02), ncol=2, frameon=False)
    for ax in axs:
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
    fig.suptitle(f"Usul: {len(u)} ta qo'shni baliq hovuzi, {sana:%Y-%m-%d} (yozuvlar — 4.5 ga dan katta hovuzlarda)",
                 fontsize=14, fontweight="bold", y=0.86)
    saqla(fig, "01_usul.png")


# ------------------------------------------------------------------ 3. NDWI gistogrammasi
def rasm_gistogramma(ndwi, ok, lab, sana):
    v = ndwi[(lab > 0) & ok]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    bins = np.linspace(-0.8, 0.8, 81)
    ax.hist(v[v <= 0], bins=bins, color=QURUQ, ec="white", lw=.4, label=f"suv emas: {100 * (v <= 0).mean():.0f}%")
    ax.hist(v[v > 0], bins=bins, color=SUV, ec="white", lw=.4, label=f"suv: {100 * (v > 0).mean():.0f}%")
    ax.axvline(0, color=CHIZIQ, lw=2, ls="--")
    ax.text(0.01, ax.get_ylim()[1] * 0.92, " chegara: NDWI = 0\n (--chegara)", color=CHIZIQ, va="top")
    ax.set_xlabel("NDWI (hovuz ichidagi har bir 10 m piksel)"); ax.set_ylabel("piksellar soni")
    ax.set_title(f"Hovuzlar ichidagi piksellar NDWI bo'yicha ikki guruhga bo'linadi ({sana:%Y-%m-%d})")
    ax.legend(frameon=False, loc="upper left")
    saqla(fig, "03_ndwi_gistogramma.png")


# ------------------------------------------------------------------ 4. vaqt qatori
def rasm_vaqt(suv, ndwi_j, tanlov):
    cols = sana_ustunlar(suv)
    t = pd.to_datetime(cols)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    ranglar = plt.cm.tab10.colors
    for k, (harf, i) in enumerate(tanlov):
        y = suv.loc[i, cols].astype(float).values; n = ndwi_j.loc[i, cols].astype(float).values
        m = np.isfinite(y)
        a1.plot(t[m], y[m], "-o", ms=3.5, lw=1.6, color=ranglar[k],
                label=f"hovuz {harf} ({suv.maydon_ga[i]:.1f} ga)")
        a2.plot(t[m], n[m], "-o", ms=3, lw=1.2, color=ranglar[k])
    a1.set_ylabel("suv bilan qoplangan, %"); a1.set_ylim(-3, 103)
    a1.legend(frameon=False, ncol=len(tanlov), loc="upper center", bbox_to_anchor=(.5, 1.13))
    a1.set_title("Har bir nuqta — bitta Sentinel-2 sanasi (shp dagi bitta ustun). Uzilish — bulutli kunlar", pad=28)
    a2.axhline(0, color=CHIZIQ, lw=1.2, ls="--"); a2.set_ylabel("NDWI")
    a2.text(t[0], 0.03, "suv chegarasi", color=CHIZIQ, fontsize=9)
    fig.autofmt_xdate()
    saqla(fig, "04_vaqt_qatori.png")


# ------------------------------------------------------------------ 5. natija jadvali (keng format)
def rasm_jadval(suv, tartib):
    cols = sana_ustunlar(suv)
    M = suv.loc[tartib, cols].astype(float).values
    fig, ax = plt.subplots(figsize=(15, 0.32 * len(tartib) + 2.2))
    cm = plt.cm.Blues.copy(); cm.set_bad(BULUT)
    im = ax.imshow(np.ma.masked_invalid(M), aspect="auto", cmap=cm, vmin=0, vmax=100, interpolation="nearest")
    qadam = max(1, len(cols) // 25)
    ax.set_xticks(range(0, len(cols), qadam)); ax.set_xticklabels(cols[::qadam], rotation=90, fontsize=8)
    harflar = [chr(65 + k) if k < 26 else str(k) for k in range(len(tartib))]
    ax.set_yticks(range(len(tartib))); ax.set_yticklabels([f"hovuz {h}" for h in harflar], fontsize=8)
    ax.spines[:].set_visible(False)
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01, label="suv bilan qoplangan, %")
    ax.set_title("Natija shp: har qator — hovuz, har ustun — sana (YYYYMMDD), katakda o'sha kungi suv foizi. "
                 "Kulrang — bulutli kun (bo'sh)", fontsize=11)
    saqla(fig, "05_natija_jadvali.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hovuz", required=True)
    ap.add_argument("--natija", required=True, help="ndwi_hovuz.py ning .gpkg chiqishi")
    ap.add_argument("--sana", help="1-rasm uchun ochiq kun (standart: hamma hovuz kuzatilgan yoz kuni)")
    a = ap.parse_args()
    hovuz = pyogrio.read_dataframe(a.hovuz)
    suv = pyogrio.read_dataframe(a.natija, layer="suv_foiz", read_geometry=False)
    nd = pyogrio.read_dataframe(a.natija, layer="ndwi", read_geometry=False)
    cols = sana_ustunlar(suv)

    rasm_sxema()

    if a.sana:
        sana = pd.Timestamp(a.sana)
    else:
        toliq = [c for c in cols if suv[c].notna().all() and c[4:6] in ("05", "06", "07", "08")]
        tarq = {c: suv[c].std() for c in toliq}
        sana = pd.Timestamp(max(tarq, key=tarq.get))
    ust = sana.strftime("%Y%m%d")
    print("1-rasm sanasi:", ust)
    u, rgb, ndwi, ok, lab, ext = s2_bolak(hovuz, sana.date())
    rasm_usul(u, rgb, ndwi, ok, lab, ext, suv[ust].values, sana)
    rasm_gistogramma(ndwi, ok, lab, sana)

    # vaqt qatori: har xil xulqli 5 hovuz (o'rtacha suv foizi va o'zgaruvchanligi bo'yicha)
    S = suv[cols].astype(float)
    kuz = S.notna().sum(axis=1)
    nomzod = suv.index[(kuz >= 0.6 * kuz.max()) & (suv.maydon_ga >= 1)]
    ort, std = S.loc[nomzod].mean(axis=1), S.loc[nomzod].std(axis=1)
    tanlov = list(dict.fromkeys([ort.idxmax(), std.idxmax(), ort.idxmin(),
                                 (ort - 50).abs().idxmin(), std.sort_values().index[len(std) // 2]]))
    tartib = list(S.mean(axis=1).sort_values(ascending=False).index)
    harf = {i: chr(65 + k) for k, i in enumerate(tartib)}
    rasm_vaqt(suv, nd, [(harf[i], i) for i in tanlov])
    rasm_jadval(suv, tartib)


if __name__ == "__main__":
    main()
