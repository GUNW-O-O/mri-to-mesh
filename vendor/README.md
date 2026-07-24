# 외부 바이너리

이 폴더는 `.gitignore` 대상이다. 커밋하지 않는다.

## dcm2niix (Windows 로컬 개발용)

컨테이너 안에서는 api 이미지가 `dcm2niix`를 포함한다. 컨테이너 밖에서
테스트하려면 직접 받아야 한다.

```powershell
New-Item -ItemType Directory -Force vendor
Invoke-WebRequest `
  -Uri "https://github.com/rordenlab/dcm2niix/releases/latest/download/dcm2niix_win.zip" `
  -OutFile "vendor/dcm2niix_win.zip"
Expand-Archive -Path "vendor/dcm2niix_win.zip" -DestinationPath "vendor" -Force
$env:DCM2NIIX_BIN = (Resolve-Path "vendor/dcm2niix.exe").Path
& $env:DCM2NIIX_BIN -h | Select-Object -First 3
```

`DCM2NIIX_BIN`을 설정하면 `dcm2niix` 마커가 붙은 테스트가 실행된다.
설정하지 않으면 해당 테스트는 skip된다.
