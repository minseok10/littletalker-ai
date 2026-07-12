#!/bin/sh
set -eu

APP_NAME="littletalker-ai"
APP_REPO="${LITTLETALKER_REPO:-https://github.com/minseok10/littletalker-ai.git}"
APP_REF="${LITTLETALKER_REF:-main}"
KAKAOCLI_REPO="${KAKAOCLI_REPO:-https://github.com/minseok10/kakaocli.git}"
KAKAOCLI_REF="${KAKAOCLI_REF:-local-build}"
INSTALL_ROOT="${LITTLETALKER_HOME:-$HOME/.local/share/$APP_NAME}"
BIN_DIR="${LITTLETALKER_BIN_DIR:-$HOME/.local/bin}"
APP_DIR="$INSTALL_ROOT/app"
KAKAOCLI_SRC="$INSTALL_ROOT/src/kakaocli"
KAKAOCLI_BIN="$INSTALL_ROOT/bin/kakaocli"
VENV_DIR="$INSTALL_ROOT/venv"
LAUNCHER="$BIN_DIR/littletalker"

info() { printf '\n==> %s\n' "$*"; }
die() { printf '\n오류: %s\n' "$*" >&2; exit 1; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

clone_or_update() {
    repo=$1
    ref=$2
    dest=$3
    label=$4

    if [ -d "$dest/.git" ]; then
        info "$label 업데이트"
        git -C "$dest" fetch --depth 1 origin "$ref"
        git -C "$dest" checkout -B "$ref" FETCH_HEAD
    elif [ -e "$dest" ]; then
        die "$dest 경로가 이미 있지만 Git 저장소가 아닙니다. 옮기거나 삭제한 뒤 다시 실행하세요."
    else
        info "$label 다운로드"
        mkdir -p "$(dirname "$dest")"
        git clone --depth 1 --branch "$ref" "$repo" "$dest"
    fi
}

[ "$(uname -s)" = "Darwin" ] || die "LittleTalker AI는 macOS에서만 설치할 수 있습니다."

if ! xcode-select -p >/dev/null 2>&1; then
    info "Xcode Command Line Tools 설치를 시작합니다"
    xcode-select --install >/dev/null 2>&1 || true
    die "표시된 창에서 설치를 마친 뒤 이 명령을 다시 실행하세요."
fi

if ! command_exists brew; then
    info "Homebrew 설치"
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi
command_exists brew || die "Homebrew를 찾을 수 없습니다. https://brew.sh 설치 후 다시 실행하세요."

info "필수 도구 확인"
command_exists git || brew install git
if ! command_exists python3 || ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
    brew install python
    hash -r
fi
command_exists sqlcipher || brew install sqlcipher
command_exists pkg-config || brew install pkgconf
command_exists swift || die "Swift가 없습니다. Xcode 또는 최신 Command Line Tools를 설치한 뒤 다시 실행하세요."

clone_or_update "$APP_REPO" "$APP_REF" "$APP_DIR" "LittleTalker AI"
clone_or_update "$KAKAOCLI_REPO" "$KAKAOCLI_REF" "$KAKAOCLI_SRC" "수정판 kakaocli ($KAKAOCLI_REF)"

info "수정판 kakaocli 빌드"
if ! (
    cd "$KAKAOCLI_SRC"
    swift build -c release --product kakaocli
); then
    die "kakaocli 빌드에 실패했습니다. Xcode/Command Line Tools를 최신 버전으로 맞춘 뒤 다시 실행하세요."
fi
mkdir -p "$(dirname "$KAKAOCLI_BIN")"
cp "$KAKAOCLI_SRC/.build/release/kakaocli" "$KAKAOCLI_BIN"
chmod 755 "$KAKAOCLI_BIN"

info "Python 환경 구성"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade anthropic

if [ ! -f "$APP_DIR/.env" ]; then
    key=${OPENROUTER_API_KEY:-}
    if [ -z "$key" ] && [ -r /dev/tty ]; then
        printf '\nOpenRouter API 키를 입력하세요 (입력 내용은 보이지 않습니다): ' >/dev/tty
        old_stty=$(stty -g </dev/tty 2>/dev/null || true)
        trap '[ -n "$old_stty" ] && stty "$old_stty" </dev/tty 2>/dev/null || true' 0 1 2 15
        stty -echo </dev/tty 2>/dev/null || true
        IFS= read -r key </dev/tty || true
        [ -n "$old_stty" ] && stty "$old_stty" </dev/tty 2>/dev/null || true
        trap - 0 1 2 15
        printf '\n' >/dev/tty
    fi
    [ -n "$key" ] || die "OpenRouter API 키가 필요합니다. OPENROUTER_API_KEY 환경변수로 전달해도 됩니다."
    case "$key" in
        *[!A-Za-z0-9_./:-]*) die "API 키에 허용되지 않는 문자가 포함되어 있습니다." ;;
    esac
    umask 077
    printf 'OPENROUTER_API_KEY=%s\n' "$key" > "$APP_DIR/.env"
fi

info "littletalker 명령 설치"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
export KAKAOCLI_BIN="$KAKAOCLI_BIN"
exec "$VENV_DIR/bin/python" "$APP_DIR/menu.py" "\$@"
EOF
chmod 755 "$LAUNCHER"

path_line='export PATH="$HOME/.local/bin:$PATH"'
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        profile="$HOME/.zprofile"
        touch "$profile"
        grep -F "$path_line" "$profile" >/dev/null 2>&1 || printf '\n# LittleTalker AI\n%s\n' "$path_line" >> "$profile"
        ;;
esac

info "설치 확인"
"$KAKAOCLI_BIN" --version || true

printf '\n설치가 끝났습니다. 새 터미널을 열고 아래 명령을 실행하세요:\n\n  littletalker\n\n'
printf '처음 사용할 때 macOS 시스템 설정에서 사용하는 터미널에\n'
printf '전체 디스크 접근 권한과 손쉬운 사용 권한을 허용해야 합니다.\n'
