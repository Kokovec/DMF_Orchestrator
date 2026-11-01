#!/bin/sh
# Make SwitchFrame build with -tags "cgo mxl" at the pinned commit.
#
# Go refuses to build a package that both uses cgo and contains Go assembly
# ("package using cgo has Go assembly file"). server/mxl/cgo.go (tag mxl) uses
# cgo, and the 10-bit V210 kernels were added to the same package as .s files.
# Upstream already keeps the 8-bit kernels in the assembly-only package
# server/mxl/v210asm; this moves the 10-bit kernels there too and has the mxl
# wrappers call v210asm.V210UnpackRow10Ptr / V210PackRow10Ptr.
# Behaviour is unchanged; only the package boundary moves.
set -eu
cd "${1:?usage: $0 <switchframe/server>}/mxl"
[ -f v210_10bit_kernels_amd64.s ] || { echo "patch not needed"; exit 0; }

for arch in amd64 arm64; do
  sed -e 's/TEXT ·v210UnpackRow10Ptr(SB)/TEXT ·V210UnpackRow10Ptr(SB)/' \
      -e 's/TEXT ·v210PackRow10Ptr(SB)/TEXT ·V210PackRow10Ptr(SB)/' \
      "v210_10bit_kernels_${arch}.s" > "v210asm/v210_10bit_kernels_${arch}.s"
  rm "v210_10bit_kernels_${arch}.s"

  cat > "v210asm/v210_10bit_kernels_${arch}.go" <<GO
//go:build ${arch}

package v210asm

// V210UnpackRow10Ptr unpacks one V210 row into planar YUV422P10 (uint16), lossless.
//
//go:noescape
func V210UnpackRow10Ptr(yOut, cbOut, crOut *uint16, v210In *byte, groups int)

// V210PackRow10Ptr packs planar YUV422P10 (uint16) into one V210 row, lossless.
//
//go:noescape
func V210PackRow10Ptr(v210Out *byte, yIn, cbIn, crIn *uint16, groups int)
GO

  f="v210_10bit_kernels_${arch}.go"
  # Drop the local assembly declarations (everything from the first
  # "...Ptr is the" comment on) and call the v210asm versions instead.
  awk '/^\/\/ v210UnpackRow10Ptr is the/ {exit} {print}' "$f" \
    | sed -e 's/^import "unsafe"$/import (\n\t"unsafe"\n\n\t"github.com\/zsiec\/switchframe\/server\/mxl\/v210asm"\n)/' \
          -e 's/^\tv210UnpackRow10Ptr(/\tv210asm.V210UnpackRow10Ptr(/' \
          -e 's/^\tv210PackRow10Ptr(/\tv210asm.V210PackRow10Ptr(/' > "$f.new"
  mv "$f.new" "$f"
done
echo "moved 10-bit V210 kernels into mxl/v210asm"
