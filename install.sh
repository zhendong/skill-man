#!/usr/bin/env sh
# skman one-line installer for macOS and Linux.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zhendong/skill-man/main/install.sh | sh
#
# Env vars:
#   SKMAN_FROM_GIT  if set, install from the GitHub repo instead of PyPI
#                   (useful for testing a branch before release)
#   SKMAN_REF       git ref to install when SKMAN_FROM_GIT is set (default: main)
#   SKMAN_REPO_URL  override the repo URL
#   SKMAN_NO_UV     if set, fall back to pip/pipx instead of uv
#   SKMAN_NO_MIGRATE  if set, skip the post-install migrate prompt
set -eu

REPO_URL="${SKMAN_REPO_URL:-https://github.com/zhendong/skill-man.git}"
REF="${SKMAN_REF:-main}"

say()  { printf '\033[1;36m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "this script is for macOS/Linux; on Windows use install.ps1" ;;
esac

# git is only required when installing from source. PyPI installs don't need it.
if [ -n "${SKMAN_FROM_GIT:-}" ]; then
  have git || die "git is required for source install (install via your package manager)"
fi

install_uv() {
  say "installing uv (single-binary Python toolchain)"
  if have curl; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif have wget; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    die "need curl or wget to install uv"
  fi
  # uv installs to ~/.local/bin or ~/.cargo/bin; make it visible to this shell
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
  done
  export PATH
}

if [ -n "${SKMAN_NO_UV:-}" ]; then
  if have pipx; then
    INSTALLER="pipx"
  elif have pip3; then
    INSTALLER="pip3"
  elif have pip; then
    INSTALLER="pip"
  else
    die "SKMAN_NO_UV set but no pipx/pip found"
  fi
else
  have uv || install_uv
  INSTALLER="uv"
fi

if [ -n "${SKMAN_FROM_GIT:-}" ]; then
  SPEC="git+${REPO_URL}@${REF}"
else
  SPEC="skman"  # PyPI
fi

say "installing skman from ${SPEC}"
case "$INSTALLER" in
  uv)   uv tool install --force "$SPEC" ;;
  pipx) pipx install --force "$SPEC" ;;
  pip*) "$INSTALLER" install --user --upgrade "$SPEC" ;;
esac

if ! have skman; then
  warn "skman not on PATH yet. Add one of these to your shell rc:"
  warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  warn "Then re-open your terminal."
  exit 0
fi

say "skman installed: $(skman --help >/dev/null 2>&1 && echo OK || echo "(run \`skman paths\`)")"

# Friendly post-install: offer to install the usage hook and migrate existing
# skills. Skipped when stdin is not a tty (piped install) — user can rerun
# manually.
if [ -t 0 ] && [ -z "${SKMAN_NO_MIGRATE:-}" ]; then
  printf '\n'
  printf 'Run `skman setup` to install the Claude Code usage hook and\n'
  printf 'migrate skills already on disk (Claude Code + skills.sh).\n'
fi
