@echo off
rem ===================================================================
rem  Conda muhitida skript ishga tushirish (conda activate shart emas).
rem    .\run.bat ndwi_hovuz.py --hovuz hovuzlar.shp --boshi 2026-03-01 --oxiri 2026-10-31
rem  Avval baliq_suv muhiti, bo'lmasa fenologiya muhiti ishlatiladi.
rem  Muhit yaratish: conda env create -f environment.yml
rem ===================================================================
set "ENV=%LOCALAPPDATA%\miniconda3\envs\baliq_suv"
if not exist "%ENV%\python.exe" set "ENV=%LOCALAPPDATA%\miniconda3\envs\fenologiya"
if not exist "%ENV%\python.exe" (
  echo Conda muhiti topilmadi: baliq_suv yoki fenologiya
  echo Yaratish: conda env create -f environment.yml
  exit /b 1
)
"%ENV%\python.exe" %*
