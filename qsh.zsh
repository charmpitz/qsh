# Resolve the runner when this file is sourced, independently of the caller's cwd.
typeset -g _QSH_RUNNER="${${(%):-%x}:A:h}/qsh.py"

qsh() {
    if ! command -v python3 >/dev/null 2>&1; then
        print -u2 -- "Python 3.10 or newer is required."
        return 1
    fi

    command python3 "$_QSH_RUNNER" "$@"
}
