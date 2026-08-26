#!/bin/bash
# Читает QR/штрихкоды с картинки через CoreImage (macOS, без внешних зависимостей).
# Использование:
#   scripts/qr.sh                 — самый свежий скриншот/фото из ~/Downloads
#   scripts/qr.sh path/to/img.png — конкретный файл
set -e
IMG="$1"
if [ -z "$IMG" ]; then
  IMG=$(ls -t ~/Downloads/*.{jpg,jpeg,png,JPG,JPEG,PNG} 2>/dev/null | head -1)
  [ -z "$IMG" ] && { echo "Не нашёл картинок в ~/Downloads"; exit 1; }
  echo "# файл: $IMG" >&2
fi
SRC=$(mktemp -t qr).swift
cat > "$SRC" <<'EOF'
import Foundation
import CoreImage
let path = CommandLine.arguments[1]
guard let img = CIImage(contentsOf: URL(fileURLWithPath: path)) else { print("не смог открыть картинку"); exit(1) }
let det = CIDetector(ofType: CIDetectorTypeQRCode, context: CIContext(), options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])!
let feats = det.features(in: img)
if feats.isEmpty { print("QR не найден") }
for f in feats { if let q = f as? CIQRCodeFeature { print(q.messageString ?? "(пусто)") } }
EOF
swift "$SRC" "$IMG"
rm -f "$SRC"
