"""NDWI va hovuz statistikasi testi (sun'iy ma'lumot). .\\run.bat test_ndwi.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ndwi_hovuz import ndwi_hisob, hovuz_statistika  # noqa: E402  (muhit shu yerda yuklanadi)
import numpy as np  # noqa: E402

# 6 piksel: suv, suv (NIR ozgina manfiy), dala, ochiq tuproq, bulut, qamralmagan (SCL 0)
yashil = np.array([0.08, 0.06, 0.07, 0.15, 0.40, np.nan], dtype="float32")
nir = np.array([0.02, -0.01, 0.35, 0.20, 0.45, np.nan], dtype="float32")
scl = np.array([6, 2, 4, 5, 9, 0])
ok, ndwi = ndwi_hisob(yashil, nir, scl)
assert ok.tolist() == [True, True, True, True, False, False], ok
assert abs(ndwi[0] - 0.6) < 1e-6 and ndwi[1] == 1.0          # (0.06+0.01)/0.05 > 1 -> 1 ga qirqiladi
assert ndwi[2] < 0 and ndwi[3] < 0

# 2 hovuz: 1-hovuz = 0..3 piksel (2 suv, 2 suv emas), 2-hovuz = 4..5 piksel (bulut, qamralmagan)
lab = np.array([1, 1, 1, 1, 2, 2])
qam, toza, suv, yig = hovuz_statistika([lab], scl > 0, ok, ndwi, 2, 0.0)
assert qam.tolist() == [4, 1] and toza.tolist() == [4, 0] and suv.tolist() == [2, 0], (qam, toza, suv)
suv_foiz = 100 * suv[0] / toza[0]
assert suv_foiz == 50.0
print("test_ndwi: OK", {"ndwi": np.round(ndwi[:4], 3).tolist(), "suv_foiz_1": suv_foiz})
