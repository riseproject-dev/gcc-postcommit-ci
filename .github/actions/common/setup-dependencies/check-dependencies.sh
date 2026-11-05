#!/usr/bin/env bash
# Provision Ubuntu self-hosted machines using the maintenance account before
# starting a runner. A CI job only checks prerequisites; it does not use sudo.
set -euo pipefail

missing=0
for tool in gcc g++ make git gh python python3 pip3 autoconf automake bison flex \
            gawk gperf cmake ninja pkg-config expect tclsh dtc zip unzip tar \
            bc patch makeinfo curl flock dpkg-query; do
  if ! command -v "$tool" >/dev/null; then
    printf 'Missing runner command: %s\n' "$tool" >&2
    missing=1
  fi
done

if command -v dpkg-query >/dev/null; then
  for package in libgmp-dev libmpfr-dev libmpc-dev zlib1g-dev libexpat1-dev \
                 libglib2.0-dev libpixman-1-dev tcl-dev python3-venv python3-pyelftools; do
    status=$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null) || status=missing
    if test "$status" != installed; then
      printf 'Missing runner package: %s\n' "$package" >&2
      missing=1
    fi
  done
fi

if ! python3 -c 'import elftools, venv'; then
  echo 'Missing runner Python modules: elftools or venv' >&2
  missing=1
fi

if test "$missing" -ne 0; then
  echo 'Provision the missing dependencies with the maintenance account before starting CI.' >&2
  exit 1
fi
echo 'Preinstalled toolchain dependencies verified.'
