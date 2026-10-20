#!/bin/sh
# Install qsh: curl -fsSL https://raw.githubusercontent.com/charmpitz/qsh/main/install.sh | sh
#
# Every check runs before anything is written. Override the defaults with
# QSH_REPO, QSH_DIR (checkout location), and QSH_BIN_DIR (where `qsh` is linked).
set -eu

QSH_REPO="${QSH_REPO:-https://github.com/charmpitz/qsh.git}"
QSH_DIR="${QSH_DIR:-$HOME/qsh}"
QSH_BIN_DIR="${QSH_BIN_DIR:-$HOME/.local/bin}"
OLLAMA_MIN_VERSION="0.40.4"
MODELS="qwen2.5-coder:1.5b-instruct-q4_K_S kev:0.8b-mxfp8"

say() { printf '%s\n' "$*"; }
fail() { printf 'qsh install: %s\n' "$*" >&2; exit 1; }

absolute() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PWD" "$1" ;;
    esac
}

# Succeeds when version $1 is at least version $2.
version_at_least() {
    awk -v a="$1" -v b="$2" 'BEGIN {
        split(a, x, "."); split(b, y, ".")
        for (i = 1; i <= 3; i++) if (x[i] + 0 != y[i] + 0) exit !(x[i] + 0 > y[i] + 0)
        exit 0
    }'
}

# The nearest existing directory at or above $1.
existing_ancestor() {
    dir="$1"
    while [ ! -d "$dir" ]; do dir=$(dirname "$dir"); done
    printf '%s\n' "$dir"
}

check_dependencies() {
    missing=""
    for tool in git python3 ollama; do
        command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
    done
    [ -z "$missing" ] || fail "missing required commands:$missing"

    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
        || fail "Python 3.10 or newer is required (found $(python3 -V 2>&1))"

    ollama list >/dev/null 2>&1 \
        || fail "cannot reach the Ollama server; start the Ollama app or run 'ollama serve'"
    version=$(ollama --version 2>/dev/null | sed -n 's/.*version is \([0-9][0-9.]*\).*/\1/p' | head -n 1)
    [ -n "$version" ] || fail "could not determine the Ollama version"
    version_at_least "$version" "$OLLAMA_MIN_VERSION" \
        || fail "Ollama $OLLAMA_MIN_VERSION or newer is required (found $version)"
}

check_install_dir() {
    if [ -e "$QSH_DIR" ]; then
        [ -d "$QSH_DIR/.git" ] && [ -f "$QSH_DIR/qsh.py" ] \
            || fail "$QSH_DIR exists and is not a qsh checkout; remove it or set QSH_DIR"
        [ -w "$QSH_DIR" ] && [ -w "$QSH_DIR/.git" ] || fail "cannot write to $QSH_DIR"
        UPDATE=1
    else
        parent=$(existing_ancestor "$QSH_DIR")
        [ -w "$parent" ] || fail "cannot create $QSH_DIR: $parent is not writable"
        UPDATE=0
    fi
}

check_bin_dir() {
    link="$QSH_BIN_DIR/qsh"
    if [ -e "$link" ] || [ -L "$link" ]; then
        [ -L "$link" ] && [ "$(readlink "$link")" = "$QSH_DIR/qsh.py" ] \
            || fail "$link already exists and is not a qsh link; remove it or set QSH_BIN_DIR"
    fi

    target=$(existing_ancestor "$QSH_BIN_DIR")
    [ -w "$target" ] || fail "cannot write to $target; set QSH_BIN_DIR to a writable directory"
}

install_qsh() {
    if [ "$UPDATE" = 1 ]; then
        say "Updating $QSH_DIR"
        git -C "$QSH_DIR" pull --ff-only --quiet
    else
        say "Cloning $QSH_REPO into $QSH_DIR"
        git clone --quiet "$QSH_REPO" "$QSH_DIR"
    fi
    [ -f "$QSH_DIR/qsh.py" ] || fail "$QSH_DIR does not contain qsh.py"
    chmod +x "$QSH_DIR/qsh.py"

    say "Linking $QSH_BIN_DIR/qsh"
    mkdir -p "$QSH_BIN_DIR"
    ln -sfn "$QSH_DIR/qsh.py" "$QSH_BIN_DIR/qsh"

    for model in $MODELS; do
        say "Pulling $model"
        ollama pull "$model"
    done

    "$QSH_BIN_DIR/qsh" --help >/dev/null || fail "installed qsh does not run"
}

main() {
    QSH_DIR=$(absolute "$QSH_DIR")
    QSH_BIN_DIR=$(absolute "$QSH_BIN_DIR")

    check_dependencies
    check_install_dir
    check_bin_dir
    install_qsh

    say "Installed qsh. Try: qsh \"list git branches\""
    case ":$PATH:" in
        *":$QSH_BIN_DIR:"*) ;;
        *) say "Note: $QSH_BIN_DIR is not on your PATH; add it or run $QSH_BIN_DIR/qsh" ;;
    esac
}

# Runs only once the whole script has been downloaded.
main "$@"
