#!/usr/bin/env bash
# Run as rise-gcc-ci after the one-shot runner exits. Reuse the pinned compiler
# and write fresh test directories; never overwrite the initial full results.
set -euo pipefail

test "$(hostname)" = rise-gcc-ci-07
test "$(id -un)" = rise-gcc-ci
if pgrep -u "$(id -u)" -f '[R]unner.Worker' >/dev/null; then
  echo 'The runner is still executing a job; wait for it to finish.' >&2
  exit 1
fi

workspace=/var/lib/rise-gcc-ci/postcommit-pilot07/_work/gcc-postcommit-ci/gcc-postcommit-ci
toolchain="$workspace/riscv-gnu-toolchain"
build="$toolchain/build"
gcc_build="$build/build-gcc-newlib-stage2/gcc"
test "$(git -C "$toolchain/gcc" rev-parse HEAD)" = 78cfe216a8e042830325df586eb179e18cd3d37f
test -x "$gcc_build/xgcc"
test -x "$gcc_build/xg++"
test -f "$gcc_build/site.exp"
export PATH="$toolchain/scripts/wrapper/qemu:$toolchain/scripts:$build/bin:$PATH"
export RISC_V_SYSROOT="$build/sysroot"

diagnostics=$(mktemp -d /var/lib/rise-gcc-ci/pilot07-recheck-XXXXXX)
printf 'Diagnostic directory: %s\n' "$diagnostics"

# Check whether the debug filename matches the test's unbounded instruction
# regexp. The source and its dg directives remain unchanged.
for xlen in 32 64; do
  abi=lp64d
  if test "$xlen" = 32; then abi=ilp32d; fi
  "$gcc_build/xgcc" -B"$gcc_build/" \
    "$toolchain/gcc/gcc/testsuite/gcc.target/riscv/pr127778.c" \
    -O3 -g -O2 "-march=rv${xlen}gcb_zicond" "-mabi=$abi" \
    -ffat-lto-objects -fno-ident -S -o "$diagnostics/pr127778-rv$xlen.s"
done
python3 - "$diagnostics" <<'PY'
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
script = r'''
set input [open [lindex $argv 0] r]
while {[gets $input line] >= 0} {
    if {[regexp {\mli} $line]} { puts "original regex: $line" }
    if {[regexp {\mli\M} $line]} { puts "bounded regex: $line" }
}
close $input
'''
program = root / "check-assembler.tcl"
program.write_text(script)
with (root / "assembler-matches.txt").open("w") as output:
    for asm in sorted(root.glob("*.s")):
        output.write(asm.name + "\n")
        output.flush()
        subprocess.run(["tclsh", str(program), str(asm)], stdout=output, check=True)
PY

architectures=(
  rv32gcv
  rv64gcv
  rv64gcv_zvbb_zvbc_zvkg_zvkn_zvknc_zvkned_zvkng_zvknha_zvknhb_zvks_zvksc_zvksed_zvksg_zvksh_zvkt
  rv64imafdcv_zicond_zawrs_zbc_zvkng_zvksg_zvbb_zvbc_zicsr_zba_zbb_zbs_zicbom_zicbop_zicboz_zfhmin_zkt
)

for index in "${!architectures[@]}"; do
  arch=${architectures[$index]}
  abi=lp64d
  if [[ "$arch" == rv32* ]]; then abi=ilp32d; fi
  # Use one fresh directory per architecture and no make jobserver, preventing
  # reused module caches across either parallel workers or multilib variants.
  suite_path=$(mktemp -d "$gcc_build/testsuite-pilot-recheck-$index-XXXXXX")
  suite=${suite_path##*/}
  flags="modules.exp plugin.exp=std-module-exports-c++20.C --target_board=riscv-sim/-march=$arch/-mabi=$abi/-mcmodel=medlow"
  printf '%s\t%s\n' "$arch" "$suite_path" >> "$diagnostics/test-directories.tsv"
  set +e
  timeout --signal=TERM --kill-after=60s 45m \
    env -u MAKEFLAGS -u MFLAGS make -C "$gcc_build" \
    "TESTSUITEDIR=$suite" "RUNTESTFLAGS=$flags" check-g++ \
    > "$diagnostics/modules-$index.log" 2>&1
  result=$?
  set -e
  printf '%s\t%s\n' "$arch" "$result" >> "$diagnostics/make-exit-codes.tsv"
  if test "$result" -ne 0; then
    echo "Diagnostic make command failed for $arch (exit $result)." >&2
    exit "$result"
  fi
  test -s "$suite_path/g++/g++.sum"
  grep -F '=== g++ Summary ===' "$suite_path/g++/g++.sum" > /dev/null
  cp "$suite_path/g++/g++.sum" "$diagnostics/modules-$index.sum"
  cp "$suite_path/g++/g++.log" "$diagnostics/modules-$index-debug.log"
done

echo "Diagnostic execution complete: $diagnostics"
echo 'Inspect the new .sum files; make success does not mean GCC tests passed.'
