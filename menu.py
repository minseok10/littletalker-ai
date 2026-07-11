#!/usr/bin/env python3
"""kakaoo 대화형 CLI 메뉴.

숫자를 입력해 메뉴를 오가며 봇을 다룬다:

    1) 봇 실행        톡방을 골라 자율응답 봇 실행 (말투 미학습 시 먼저 학습)
    2) 말투 학습      톡방을 골라 STYLE.md/examples.txt 갱신
    3) 톡방 목록 보기  카톡 톡방 목록 + 학습 상태
    4) 톡방별 설정     이름·학습 방식 등 config.env 확인/변경
    0) 종료

기존 kakao_bot.py / update_style.py 를 그대로 재사용한다
(같은 venv 파이썬으로 subprocess 실행하므로 두 스크립트는 건드리지 않는다).

실행:
    .venv/bin/python menu.py
"""
import os
import sys
import subprocess

import kakao_bot as kb   # .env 로드, kakaocli, ROOMS_DIR, sanitize_room, update_config_value 재사용
import update_style as us  # infer_name_for_target (이름 자동 추론) 재사용


# ── update_style.py 학습 파라미터 ────────────────────────────────
# (config.env 키, update_style CLI 플래그, 기본값, 숫자여부, 설명)
# 봇의 FETCH_LIMIT(한 사이클 조회량)와 겹치지 않도록 학습용은 STYLE_FETCH_LIMIT 로 저장.
LEARN_PARAMS = [
    ("MY_MESSAGES",       "--my-messages", "180",  True,  "분석에 쓸 내 메시지 최대 개수"),
    ("PAIRS",             "--pairs",       "25",   True,  "examples.txt 대화 쌍 수"),
    ("STYLE_FETCH_LIMIT", "--fetch-limit", "6000", True,  "역추적해 읽을 전체 메시지 수"),
    ("STYLE_MODEL",       "--model",
     os.environ.get("STYLE_MODEL", kb.DEFAULTS["STYLE_MODEL"]), False,
     "말투 갱신 모델(OpenRouter 슬러그)"),
]


# ── 작은 유틸 ────────────────────────────────────────────────────
def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def room_dir(target):
    return os.path.join(kb.ROOMS_DIR, kb.sanitize_room(target))


def style_exists(target):
    return os.path.exists(os.path.join(room_dir(target), "STYLE.md"))


def room_target(chat_id, name):
    """이 톡방에 쓸 target(= rooms/ 폴더 키)을 정한다.
    기존에 이름 기반 폴더에 말투가 있으면 호환을 위해 그 이름을,
    아니면 이름이 바뀌어도 안정적인 chat_id 를 쓴다."""
    name_dir = os.path.join(kb.ROOMS_DIR, kb.sanitize_room(name))
    if os.path.exists(os.path.join(name_dir, "STYLE.md")):
        return name
    return chat_id


def read_config(target):
    return kb.parse_env_file(os.path.join(room_dir(target), "config.env"))


def set_config_value(target, key, value):
    """rooms/<target>/config.env 에서 key 를 갱신(없으면 추가)한다."""
    kb.update_config_value(os.path.join(room_dir(target), "config.env"), key, value)


def list_chats():
    """kakaocli chats -> [(chat_id, name, display)]. display 는 원문(시각 포함)."""
    out = kb.kakaocli(["chats"])
    chats = []
    for line in out.splitlines():
        line = line.rstrip()
        if not (line.startswith("[") and "]" in line):
            continue
        cid = line[1:line.index("]")]
        rest = line[line.index("]") + 1:].strip()
        name = rest.rsplit(" ", 1)[0] if " " in rest else rest
        chats.append((cid, name, rest))
    return chats


def run_script(script, extra_args):
    """하위 스크립트를 실행하고 성공 여부를 반환한다. 출력은 그대로 흘려보낸다."""
    cmd = [sys.executable, os.path.join(kb.HERE, script)] + extra_args
    print("\n$ " + " ".join(cmd) + "\n")
    try:
        result = subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n(중단됨 — 메뉴로 돌아갑니다)")
        return False
    if result.returncode != 0:
        print(f"\n(실행 실패 — 종료 코드 {result.returncode})")
        return False
    return True


# ── 공통: 톡방 선택 ───────────────────────────────────────────────
def pick_chat():
    """톡방 목록을 번호로 보여주고 하나 고른다. -> (chat_id, name) 또는 None."""
    try:
        chats = list_chats()
    except Exception as e:
        print(f"톡방 목록을 불러오지 못했습니다: {e}")
        return None
    if not chats:
        print("톡방이 없습니다. (kakaocli 연결/권한을 확인하세요)")
        return None
    print()
    for i, (cid, name, disp) in enumerate(chats, 1):
        mark = "✓ 학습" if style_exists(room_target(cid, name)) else "· 미학습"
        print(f"  {i:>2}) {disp}   [{mark}]")
    print("   0) 뒤로")
    sel = ask("\n번호 선택: ")
    if sel in ("", "0"):
        return None
    if not sel.isdigit() or not (1 <= int(sel) <= len(chats)):
        print("잘못된 번호입니다.")
        return None
    cid, name, _ = chats[int(sel) - 1]
    return cid, name


# ── 학습 파라미터 ────────────────────────────────────────────────
def show_learn_params(target):
    cfg = read_config(target)
    print("\n=== 학습 파라미터 ===")
    for key, _flag, default, _num, desc in LEARN_PARAMS:
        cur = cfg.get(key, default)
        star = " *" if key in cfg else ""   # * = config.env에 저장된 값
        print(f"  {key} = {cur}{star}   ({desc})")
    include = kb.truthy(cfg.get("INCLUDE_BOT_MESSAGES", "false"))
    print(f"  INCLUDE_BOT_MESSAGES = {str(include).lower()}"
          "   (기존 봇 발화를 말투·행동 학습에 포함)")


def learn_args(target):
    """현재(저장값 또는 기본값) 학습 파라미터를 update_style CLI 인수로 조립."""
    cfg = read_config(target)
    args = []
    for key, flag, default, _num, _desc in LEARN_PARAMS:
        args += [flag, str(cfg.get(key, default))]
    if kb.truthy(cfg.get("INCLUDE_BOT_MESSAGES", "false")):
        args.append("--include-bot-messages")
    else:
        args.append("--exclude-bot-messages")
    return args


def edit_learn_params(target):
    """학습 파라미터를 보여주고 변경분을 config.env에 저장한다."""
    show_learn_params(target)
    if ask("\n값을 조정할까요? (y/N): ").lower() not in ("y", "yes"):
        return
    for key, _flag, default, is_num, desc in LEARN_PARAMS:
        cur = read_config(target).get(key, default)
        new = ask(f"  {key} [{cur}] ({desc}) — Enter=유지, 새 값: ").strip()
        if not new:
            continue
        if is_num and (not new.isdigit() or int(new) < 1):
            print("    → 1 이상의 숫자만 가능. 유지합니다.")
            continue
        set_config_value(target, key, new)
        print(f"    → {key} = {new} 저장")


def choose_bot_message_learning(target):
    """기존 봇 발화를 다음 학습에 포함할지 선택하고 톡방 설정에 저장한다."""
    current = kb.truthy(read_config(target).get("INCLUDE_BOT_MESSAGES", "false"))
    label = "포함" if current else "제외"
    print(f"\n현재 봇 발화 학습 설정: {label}")
    sel = ask("기존 봇 발화를 학습에 포함할까요? (y=포함 / n=제외 / Enter=유지): ").lower()
    if sel in ("y", "yes"):
        current = True
    elif sel in ("n", "no"):
        current = False
    elif sel != "":
        print("잘못된 입력 — 기존 설정을 유지합니다.")
    set_config_value(target, "INCLUDE_BOT_MESSAGES", str(current).lower())
    print("→ 봇 발화를 " + ("포함합니다." if current else "제외합니다."))
    return current


# ── 봇 이름/별명 ─────────────────────────────────────────────────
def edit_identity(target):
    """대표 이름(BOT_NAME)과 이름/별명 목록(BOT_ALIASES)을 직접 고친다.
    (보통은 말투 학습 때 Opus가 대화에서 자동 추출한다.)"""
    cfg = read_config(target)
    print(f"\n현재 대표 이름 : {cfg.get('BOT_NAME') or '(미설정)'}")
    print(f"현재 이름/별명 : {cfg.get('BOT_ALIASES') or '(없음)'}")
    print("이름/별명은 보통 말투 학습에서 대화를 보고 자동 추출됩니다. 여기서 직접 고칠 수도 있어요.")
    n = ask("대표 이름 (Enter=유지): ").strip()
    a = ask("이름/별명 쉼표로 구분 (Enter=유지): ").strip()
    if n:
        set_config_value(target, "BOT_NAME", n)
    if a:
        set_config_value(target, "BOT_ALIASES", a)
    print("저장했습니다." if (n or a) else "변경 없음.")


# ── 메뉴 동작 ────────────────────────────────────────────────────
def do_learn(chat=None):
    if chat is None:
        chat = pick_chat()
    if chat is None:
        return False
    cid, name = chat
    target = room_target(cid, name)
    # 이름·별명·프로필은 학습 중 Opus가 이 톡방 대화에서 추출한다. 대표 이름을 직접 지정하려면 입력.
    try:
        hint = us.infer_name_for_target(target)
    except Exception:
        hint = ""
    hint_txt = f" (표시이름 추정: {hint})" if hint else ""
    override = ask(f"대표 이름 직접 지정{hint_txt} — Enter=대화에서 자동 추출, 입력=지정: ").strip()
    edit_learn_params(target)
    choose_bot_message_learning(target)
    print(f"\n[{name}] 말투·행동·이름·프로필 학습을 시작합니다...")
    name_args = ["--name", override] if override else []
    return run_script("update_style.py", ["--target", target] + name_args + learn_args(target))


def do_run():
    chat = pick_chat()
    if chat is None:
        return
    cid, name = chat
    target = room_target(cid, name)

    # 1) 말투가 없으면 먼저 학습
    if not style_exists(target):
        print(f"\n[{name}] 은 아직 말투(STYLE.md)를 학습하지 않았습니다.")
        yn = ask("봇 실행 전에 먼저 말투를 학습할까요? (Y/n): ").lower()
        learned = False
        if yn in ("", "y", "yes"):
            learned = do_learn((cid, name))
        if not learned or not style_exists(target):
            print("말투 학습이 완료되지 않아 봇 실행을 취소합니다.")
            return

    # 2) 전송 대상 — 기본은 나와의 채팅, 실제 톡방만 이중 확인
    print("\n전송 방식을 선택하세요:")
    print("  1 = 나와의 채팅으로 전송 (기본·테스트용)")
    print("  2 = 선택한 실제 톡방에 전송")
    print("  3 = dry-run (전송 안 함, 초안만 로그)")
    delivery = ask("선택 (1/2/3, 기본 1): ")
    if delivery == "2":
        confirm = ask("실제 톡방에 전송합니다. 진행하려면 'yes'를 입력: ").strip().lower()
        if confirm == "yes":
            delivery_args = ["--no-dry-run", "--no-use-self"]
        else:
            print("→ 나와의 채팅으로 전송합니다.")
            delivery_args = ["--no-dry-run", "--use-self"]
    elif delivery == "3":
        delivery_args = ["--dry-run", "--no-use-self"]
    else:
        if delivery not in ("", "1"):
            print("잘못된 입력 — 나와의 채팅으로 전송합니다.")
        delivery_args = ["--no-dry-run", "--use-self"]

    args = ["--target", target] + delivery_args

    # 3) 실행 방식 — 실행할 때마다 선택
    print("\n실행 방식:")
    print("  1 = 한 사이클만 실행 후 메뉴로 복귀")
    print("  2 = 루프로 지속 감시 (Ctrl+C 로 중단)")
    if ask("선택 (1/2, 기본 1): ") == "2":
        args.append("--loop")

    run_script("kakao_bot.py", args)


def do_list():
    try:
        chats = list_chats()
    except Exception as e:
        print(f"톡방 목록을 불러오지 못했습니다: {e}")
        return
    print("\n=== 톡방 목록 ===")
    for i, (cid, name, disp) in enumerate(chats, 1):
        target = room_target(cid, name)
        if style_exists(target):
            status = "학습됨"
        else:
            status = "미학습"
        print(f"  {i:>2}) {disp}")
        print(f"        id={cid}   {status}")


def do_settings():
    chat = pick_chat()
    if chat is None:
        return
    cid, name = chat
    target = room_target(cid, name)
    while True:
        cfg = read_config(target)
        include_bot = kb.truthy(cfg.get("INCLUDE_BOT_MESSAGES", "false"))
        print(f"\n=== [{name}] 설정 ===")
        print(f"  room 폴더  : rooms/{kb.sanitize_room(target)}")
        print(f"  말투 학습  : {'예' if style_exists(target) else '아니오'}")
        print(f"  봇 이름    : {cfg.get('BOT_NAME') or '(미설정 — 일반 문구)'}")
        print(f"  이름/별명  : {cfg.get('BOT_ALIASES') or '(없음)'}")
        print(f"  봇 발화 학습: {'포함' if include_bot else '제외'}")
        if cfg:
            print("  config.env :")
            for k, v in cfg.items():
                print(f"      {k}={v}")
        else:
            print("  config.env : (없음 — 기본값 사용)")
        print("\n  1) 봇 이름/별명 변경")
        print("  2) 학습 파라미터 변경")
        print("  3) 봇 발화 포함/제외 변경")
        print("  4) 말투 다시 학습")
        print("  0) 뒤로")
        sel = ask("선택: ")
        if sel == "1":
            edit_identity(target)
        elif sel == "2":
            edit_learn_params(target)
        elif sel == "3":
            choose_bot_message_learning(target)
        elif sel == "4":
            do_learn((cid, name))
        elif sel in ("", "0"):
            return
        else:
            print("잘못된 입력입니다.")


# ── 메인 루프 ────────────────────────────────────────────────────
MENU = [
    ("1", "봇 실행", do_run),
    ("2", "말투 학습", lambda: do_learn()),
    ("3", "톡방 목록 보기", do_list),
    ("4", "톡방별 설정 보기/변경", do_settings),
]


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("⚠️  OPENROUTER_API_KEY 가 설정되지 않았습니다 (.env 확인).")
        print("    톡방 목록 보기는 되지만, 봇 실행/말투 학습은 실패할 수 있습니다.")

    actions = {k: fn for k, _, fn in MENU}
    while True:
        print("\n" + "=" * 34)
        print("   kakaoo — 카톡 자율응답 봇")
        print("=" * 34)
        for k, label, _ in MENU:
            print(f"  {k}) {label}")
        print("  0) 종료")
        try:
            sel = input("\n메뉴 번호: ").strip()
        except EOFError:      # Ctrl+D / 닫힌 입력 → 종료 (무한 루프 방지)
            print("\n종료합니다.")
            return
        if sel in ("0", "q", "quit", "exit"):
            print("종료합니다.")
            return
        fn = actions.get(sel)
        if not fn:
            print("잘못된 입력입니다.")
            continue
        try:
            fn()
        except KeyboardInterrupt:
            print("\n(취소됨 — 메뉴로 돌아갑니다)")
        except Exception as e:
            print(f"오류: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
