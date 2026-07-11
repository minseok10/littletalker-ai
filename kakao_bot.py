#!/usr/bin/env python3
"""
LittleTalker AI — 카톡 단톡방 자율 응답 봇

각 톡방마다 rooms/<톡방이름>/ 폴더에 말투·상태·로그를 분리 보관한다:
    rooms/<TARGET>/STYLE.md       말투 프로파일 (톡방별)
    rooms/<TARGET>/examples.txt   대화 예시 (톡방별)
    rooms/<TARGET>/state.json     처리 상태 (dedup, 발화 이력)
    rooms/<TARGET>/bot_log.jsonl  로그
    rooms/<TARGET>/config.env     이 톡방 전용 설정 (선택)

설정 우선순위:  명령줄 인수  >  환경변수(.env/셸)  >  톡방 config.env  >  내장 기본값

사용 예:
    python3 kakao_bot.py --target "내톡방"              # 한 사이클
    python3 kakao_bot.py --target "내톡방" --loop       # 지속 가동
    python3 kakao_bot.py --target "내톡방" --no-dry-run --context-limit 50 --poll-seconds 8
    DRY_RUN=true TARGET="내톡방" python3 kakao_bot.py    # 환경변수로도 가능

킬 스위치: 디렉토리에 STOP 파일(전체) 또는 rooms/<톡방>/STOP(해당 톡방만)이 있으면 종료.
"""
import os
import re
import sys
import json
import time
import random
import argparse
import subprocess
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOMS_DIR = os.path.join(HERE, "rooms")
GLOBAL_ENV = os.path.join(HERE, ".env")
GLOBAL_STOP = os.path.join(HERE, "STOP")
SENT_ID_HISTORY_LIMIT = 10000  # 말투 재학습 때 과거 봇 발화를 포함/제외하기 위한 이력

# 내장 기본값
DEFAULTS = {
    "TARGET": "내톡방",
    "MODEL": "anthropic/claude-sonnet-5",      # 봇 응답 판단 기본 모델(OpenRouter)
    "STYLE_MODEL": "anthropic/claude-opus-4.8", # 말투 갱신 기본 모델(OpenRouter)
    "DRY_RUN": True,        # 안전 기본값. 실제 전송하려면 false 로.
    "USE_SELF": False,      # true면 전송을 자기채팅으로 (읽기는 TARGET)
    "CONTEXT_LIMIT": 40,    # LLM에 넘기는 최근 맥락 메시지 수
    "POLL_SECONDS": 10,     # 루프 폴링 주기(초)
    "DELAY_MIN": 3,         # 응답 전 랜덤 대기 하한(초)
    "DELAY_MAX": 9,         # 상한
    "MIN_GAP": 0,           # 선택적 안전 간격(0=STYLE.md의 대화 리듬을 제한하지 않음)
    "MAX_PER_HOUR": 0,      # 시간당 최대 발화 수 (0=무제한)
    "FETCH_LIMIT": 80,      # 한 번에 읽어올 메시지 수
    "BOT_NAME": "",         # 봇이 흉내낼 사람 대표 이름(비우면 일반 문구; 보통 학습 시 자동 설정)
    "BOT_ALIASES": "",      # 이 톡방에서 나를 부르는 이름/별명들(쉼표 구분; 학습 시 자동 추출)
}


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def parse_env_file(path):
    d = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    return d


# 전역 .env 를 환경변수로 올린다(이미 설정된 셸 환경은 유지)
for _k, _v in parse_env_file(GLOBAL_ENV).items():
    os.environ.setdefault(_k, _v)


def sanitize_room(name):
    """톡방 이름을 폴더명으로. 경로 구분자 등만 치환."""
    return name.replace("/", "_").replace("\\", "_").strip()


class Config:
    """해석된 설정값 + 톡방별 경로 묶음."""
    def __init__(self, args):
        room_cfg = {}  # 톡방 config.env (target 확정 후 로드)

        def resolve(key, arg_attr, cast):
            # 우선순위: CLI 인수 > 환경변수 > 톡방 config.env > 기본값
            av = getattr(args, arg_attr, None)
            if av is not None:
                return av
            if key in os.environ:
                return cast(os.environ[key])
            if key in room_cfg:
                return cast(room_cfg[key])
            return DEFAULTS[key]

        # target 먼저 (톡방 config.env 위치를 정해야 하므로)
        self.target = (args.target if args.target is not None
                       else os.environ.get("TARGET", DEFAULTS["TARGET"]))
        self.room_dir = os.path.join(ROOMS_DIR, sanitize_room(self.target))
        room_cfg = parse_env_file(os.path.join(self.room_dir, "config.env"))

        self.model = resolve("MODEL", "model", str)
        self.dry_run = resolve("DRY_RUN", "dry_run", truthy)
        self.use_self = resolve("USE_SELF", "use_self", truthy)
        self.context_limit = resolve("CONTEXT_LIMIT", "context_limit", int)
        self.poll_seconds = resolve("POLL_SECONDS", "poll_seconds", int)
        self.delay_min = resolve("DELAY_MIN", "delay_min", int)
        self.delay_max = resolve("DELAY_MAX", "delay_max", int)
        self.min_gap = resolve("MIN_GAP", "min_gap", int)
        self.max_per_hour = resolve("MAX_PER_HOUR", "max_per_hour", int)
        self.fetch_limit = resolve("FETCH_LIMIT", "fetch_limit", int)
        self.bot_name = resolve("BOT_NAME", "name", str)
        self.bot_aliases = resolve("BOT_ALIASES", "aliases", str)

        # 톡방별 파일 경로
        self.style_path = os.path.join(self.room_dir, "STYLE.md")
        self.examples_path = os.path.join(self.room_dir, "examples.txt")
        self.state_path = os.path.join(self.room_dir, "state.json")
        self.log_path = os.path.join(self.room_dir, "bot_log.jsonl")
        self.room_stop = os.path.join(self.room_dir, "STOP")

        # 런타임에 채워짐
        self.chat_id = None
        self.send_name = None

        os.makedirs(self.room_dir, exist_ok=True)


def log(cfg, event, **fields):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with open(cfg.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[{event}] " + " ".join(f"{k}={v!r}" for k, v in fields.items()))


def load_state(cfg):
    # 구버전 state.json에 없는 키는 기본값으로 채운다
    state = {
        "last_processed_id": 0,
        "responded_ids": [],
        "sent_ids": [],
        "last_sent_ts": None,
        "sent_timestamps": [],
    }
    if os.path.exists(cfg.state_path):
        with open(cfg.state_path, encoding="utf-8") as f:
            state.update(json.load(f))
    return state


def save_state(cfg, state):
    tmp = cfg.state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg.state_path)


def kakaocli(args):
    res = subprocess.run(["kakaocli"] + args, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"kakaocli {args} 실패: {res.stderr.strip()}")
    return res.stdout


def resolve_chat(target):
    """target -> (chat_id, send_name). 읽기는 id, 전송은 이름.
    이름은 정확 매칭을 우선하고, 없으면 부분 매칭으로 fallback한다
    ("A톡방"과 "A톡방2"가 같이 있을 때 엉뚱한 쪽으로 매칭되는 것 방지)."""
    out = kakaocli(["chats"])
    partial = None
    for line in out.splitlines():
        if not (line.startswith("[") and "]" in line):
            continue
        cid = line[1:line.index("]")]
        rest = line[line.index("]") + 1:].strip()
        name = rest.rsplit(" ", 1)[0] if " " in rest else rest
        if target.isdigit():
            if cid == target:
                return cid, name
        else:
            if target in (rest, name):              # 정확 매칭 우선
                return cid, target
            if partial is None and target in rest:  # 부분 매칭은 첫 건만 보관
                partial = (cid, name)               # 전송은 방 전체 이름으로
    if partial:
        return partial
    raise RuntimeError(f"TARGET '{target}' 채팅을 찾지 못함")


def fetch_messages(chat_id, limit):
    out = kakaocli(["messages", "--chat-id", str(chat_id), "--limit", str(limit), "--json"])
    msgs = json.loads(out)
    msgs.sort(key=lambda m: m["id"])
    return msgs


def read_text_file(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""


CONFIG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def write_text_atomic(path, text):
    """임시 파일에 쓰고 원자적으로 교체(부분 기록·손상 방지)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def sanitize_config_value(value):
    """config.env 값의 '한 줄' 불변식 보장 — 개행/제어문자 제거(키 주입 방지)."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()


def update_config_value(path, key, value):
    """config.env 파일에서 key 를 갱신(없으면 추가). 주석·다른 키는 보존한다.
    key 형식을 검증하고 value의 개행/제어문자를 제거해, LLM/외부 입력이 별도 키를
    주입하지 못하게 막는다(예: value='민석\\nDRY_RUN=false' 방어)."""
    if not CONFIG_KEY_RE.match(key):
        raise ValueError(f"잘못된 config 키: {key!r}")
    value = sanitize_config_value(value)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    lines, found = [], False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"{key}={value}\n")
    write_text_atomic(path, "".join(lines))


def remove_config_keys(path, keys):
    """config.env에서 지정한 키를 제거하고 다른 내용은 보존한다."""
    if not os.path.exists(path):
        return
    keys = set(keys)
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                key = s.split("=", 1)[0].strip()
                if key in keys:
                    continue
            lines.append(line if line.endswith("\n") else line + "\n")
    write_text_atomic(path, "".join(lines))


def make_anthropic_client(**kwargs):
    """Anthropic SDK를 OpenRouter Anthropic API로 라우팅한다. OpenRouter 전용."""
    import anthropic
    return anthropic.Anthropic(
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://openrouter.ai/api"),
        auth_token=os.environ["OPENROUTER_API_KEY"],
        **kwargs,
    )


def build_system_prompt(style, examples, name="", aliases=""):
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    if not alias_list and name:
        alias_list = [name]
    persona = f'"{name}"' if name else "이 계정 주인"
    name_rule = (f"내 이름/별명({', '.join(alias_list)} 등)이 언급됨"
                 if alias_list else "내 이름/별명이 언급됨")
    return f"""너는 카톡 단톡방에서 {persona}의 말투로 대신 답하는 봇이다.
아래 STYLE.md는 {persona}의 관찰된 말투 규칙이고, examples.txt는 실제 대화 예시다.
이 사람의 말투뿐 아니라 언제 말하고 언제 침묵하는지, 얼마나 자주 끼어드는지,
몇 개로 끊어 보내는지까지 아래 STYLE.md에 기록된 행동 패턴을 그대로 재현하라.

[행동 판단의 유일한 기준]
- 응답 여부, 발화 빈도, 먼저 말 거는 정도, 질문·이름 언급·잡담에 반응하는 방식,
  티키타카 지속 정도, 메시지 길이와 끊어 보내기는 모두 STYLE.md를 따른다.
- 보편적인 "적극적/소극적" 기본값을 임의로 적용하지 않는다.
- STYLE.md상 이 사람이 해당 상황에서 침묵할 가능성이 높으면 should_respond=false로 판단한다.
- 응답한다면 말투·어미·이모지·문장부호·메시지 개수도 STYLE.md와 examples.txt를 따른다.
- 이름/별명 참고: {name_rule}

--- STYLE.md ---
{style}

--- examples.txt (직전 메시지 → 내 답장) ---
{examples}
"""


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "should_respond": {"type": "boolean"},
        "reason": {"type": "string"},
        "messages": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["should_respond", "reason", "messages"],
    "additionalProperties": False,
}


def decide(client, cfg, system_prompt, context_msgs):
    me_label = f"나({cfg.bot_name})" if cfg.bot_name else "나"
    me_ref = f"내가({cfg.bot_name})" if cfg.bot_name else "내가"
    convo_lines = []
    for m in context_msgs:
        who = me_label if m.get("is_from_me") else m.get("sender", "?")
        convo_lines.append(f"[{who}] {m.get('text','')}")
    convo = "\n".join(convo_lines)
    user_content = (
        "아래는 단톡방의 최근 대화다. 맨 아래가 가장 최신 메시지.\n"
        f"{me_ref} 지금 끼어들어 말하는 게 자연스러운지 판단하고, "
        "응답한다면 내 말투로 초안을 써라. 침묵이 자연스러우면 should_respond=false.\n\n"
        f"{convo}"
    )
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


def send_message(cfg, text):
    if cfg.use_self:
        kakaocli(["send", "self", text, "--me"])
    else:
        kakaocli(["send", cfg.send_name, text])


def within_rate_limit(cfg, state):
    now = datetime.now(timezone.utc)
    recent = [t for t in state["sent_timestamps"]
              if datetime.fromisoformat(t) > now - timedelta(hours=1)]
    state["sent_timestamps"] = recent
    if cfg.max_per_hour > 0 and len(recent) >= cfg.max_per_hour:
        return False, f"시간당 상한({cfg.max_per_hour}) 도달"
    if state["last_sent_ts"]:
        gap = (now - datetime.fromisoformat(state["last_sent_ts"])).total_seconds()
        if gap < cfg.min_gap:
            return False, f"마지막 발화 후 {int(gap)}s < {cfg.min_gap}s"
    return True, ""


def my_last_utterance_gap(messages):
    mine = [m for m in messages if m.get("is_from_me")]
    if not mine:
        return None
    ts = datetime.fromisoformat(mine[-1]["timestamp"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - ts).total_seconds()


def stop_requested(cfg):
    return os.path.exists(GLOBAL_STOP) or os.path.exists(cfg.room_stop)


def bot_sent_ids(before_msgs, after_msgs, sent_count):
    """전송 후 '봇이 방금 보낸' is_from_me id만 골라낸다.
    사이클 시작 시점(before_msgs)에 이미 있던 내 메시지는 제외해, 내가 직접 친
    메시지가 봇 발화로 오분류되어 말투 학습에서 빠지는 것을 막는다.
    새로 생긴 것 중 가장 최근 sent_count개만 봇 발화로 본다(전송 창에 낀 수동 입력 방어).
    use-self 전송이면 대상 톡방엔 새 발화가 없어 빈 리스트가 된다."""
    if sent_count <= 0:
        return []
    before = {m["id"] for m in before_msgs if m.get("is_from_me")}
    new_mine = sorted(m["id"] for m in after_msgs
                      if m.get("is_from_me") and m["id"] not in before)
    return new_mine[-sent_count:]


def run_cycle(client, cfg, system_prompt, state):
    if stop_requested(cfg):
        log(cfg, "STOP", msg="STOP 파일 감지, 종료")
        return "stop"

    messages = fetch_messages(cfg.chat_id, cfg.fetch_limit)
    if not messages:
        log(cfg, "EMPTY")
        return "ok"

    new_others = [
        m for m in messages
        if m["id"] > state["last_processed_id"]
        and not m.get("is_from_me")
        and m["id"] not in state["sent_ids"]
        and m.get("text")
    ]
    newest_id = max(m["id"] for m in messages)

    if not new_others:
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        log(cfg, "NO_NEW", newest_id=newest_id)
        return "ok"

    trigger = new_others[-1]
    if trigger["id"] in state["responded_ids"]:
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        log(cfg, "ALREADY_RESPONDED", id=trigger["id"])
        return "ok"

    gap = my_last_utterance_gap(messages)
    if gap is not None and gap < cfg.min_gap:
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        log(cfg, "TOO_SOON_AFTER_ME", gap=int(gap))
        return "ok"

    ok, why = within_rate_limit(cfg, state)
    if not ok:
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        log(cfg, "RATE_LIMITED", reason=why)
        return "ok"

    context_msgs = messages[-cfg.context_limit:]
    try:
        decision = decide(client, cfg, system_prompt, context_msgs)
    except Exception as e:
        # 응답 판단/JSON 파싱 실패(예: max_tokens 잘림, 빈 응답). 같은 trigger로
        # 매 사이클 재호출(비용 누적)하지 않도록 상태를 전진시키고 넘어간다.
        state["responded_ids"].append(trigger["id"])
        state["responded_ids"] = state["responded_ids"][-200:]
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        log(cfg, "DECIDE_FAILED", trigger_id=trigger["id"], error=str(e))
        return "ok"
    drafts = [d.strip() for d in decision.get("messages", []) if d.strip()]

    log(cfg, "DRAFT", trigger_id=trigger["id"], trigger_text=trigger.get("text"),
        should_respond=decision.get("should_respond"),
        reason=decision.get("reason"), drafts=drafts)

    state["responded_ids"].append(trigger["id"])
    state["responded_ids"] = state["responded_ids"][-200:]

    if not decision.get("should_respond") or not drafts:
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        log(cfg, "SILENT", reason=decision.get("reason"))
        return "ok"

    if cfg.dry_run:
        log(cfg, "SKIP", mode="DRY_RUN", drafts=drafts)
        state["last_processed_id"] = newest_id
        save_state(cfg, state)
        return "ok"

    delay = random.randint(min(cfg.delay_min, cfg.delay_max),
                           max(cfg.delay_min, cfg.delay_max))
    log(cfg, "WAIT", seconds=delay)
    time.sleep(delay)
    if stop_requested(cfg):
        log(cfg, "STOP", msg="대기 중 STOP 감지, 전송 취소")
        return "stop"

    sent_count = 0
    try:
        for i, text in enumerate(drafts):
            send_message(cfg, text)
            now = datetime.now(timezone.utc)
            state["last_sent_ts"] = now.isoformat()
            state["sent_timestamps"].append(now.isoformat())
            sent_count += 1
            log(cfg, "SENT", idx=i, text=text)
            if i < len(drafts) - 1:
                time.sleep(random.uniform(1.5, 4.0))
    except Exception as e:
        # 다중 메시지 중 일부만 나가고 실패해도, 이미 나간 내 메시지는
        # 아래 재조회로 sent_ids에 반드시 기록한다(루프 방지·말투 학습 오염 방지).
        log(cfg, "SEND_FAILED", idx=sent_count, error=str(e))

    if sent_count > 0:
        try:
            # 이 사이클 시작 때 조회한 messages 를 '전송 전' 기준으로 삼아,
            # 봇이 방금 보낸 메시지 id만 sent_ids 에 기록한다(내 기존 메시지 제외).
            after = fetch_messages(cfg.chat_id, 20)
            for mid in bot_sent_ids(messages, after, sent_count):
                if mid not in state["sent_ids"]:
                    state["sent_ids"].append(mid)
            state["sent_ids"] = state["sent_ids"][-SENT_ID_HISTORY_LIMIT:]
        except Exception as e:
            log(cfg, "WARN", msg=f"전송 후 재조회 실패: {e}")

    state["last_processed_id"] = max(newest_id, max(state["sent_ids"], default=newest_id))
    save_state(cfg, state)
    return "ok"


def build_arg_parser():
    p = argparse.ArgumentParser(description="LittleTalker AI — 카톡 단톡방 자율 응답 봇")
    p.add_argument("--target", help="톡방 이름(부분일치) 또는 chat-id")
    p.add_argument("--model")
    p.add_argument("--context-limit", dest="context_limit", type=int, help="LLM 맥락 메시지 수")
    p.add_argument("--poll-seconds", dest="poll_seconds", type=int, help="루프 폴링 주기(초)")
    p.add_argument("--delay-min", dest="delay_min", type=int, help="응답 전 랜덤 대기 하한(초)")
    p.add_argument("--delay-max", dest="delay_max", type=int, help="응답 전 랜덤 대기 상한(초)")
    p.add_argument("--min-gap", dest="min_gap", type=int, help="내 마지막 발화 후 최소 간격(초)")
    p.add_argument("--max-per-hour", dest="max_per_hour", type=int, help="시간당 최대 발화(0=무제한)")
    p.add_argument("--fetch-limit", dest="fetch_limit", type=int, help="한 번에 읽는 메시지 수")
    p.add_argument("--name", help="봇이 흉내낼 사람 이름(비우면 일반 문구/학습 시 자동값)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                   help="실제 전송 안 함(초안만)")
    p.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                   help="실제 전송함")
    p.add_argument("--use-self", dest="use_self", action="store_true", default=None,
                   help="전송을 자기채팅으로(읽기는 TARGET)")
    p.add_argument("--no-use-self", dest="use_self", action="store_false",
                   help="전송을 TARGET 톡방으로")
    p.add_argument("--loop", action="store_true", help="지속 가동(폴링 루프)")
    return p


def main():
    args = build_arg_parser().parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY 가 필요합니다 (.env 또는 환경변수).", file=sys.stderr)
        sys.exit(1)

    cfg = Config(args)

    client = make_anthropic_client(max_retries=4)  # 일시적 과부하(529)·네트워크 오류 자동 재시도

    style = read_text_file(cfg.style_path)
    examples = read_text_file(cfg.examples_path)
    if not style:
        print(f"WARN: {cfg.style_path} 가 없음. update_style.py 로 먼저 생성하세요.", file=sys.stderr)
    system_prompt = build_system_prompt(style, examples, cfg.bot_name, cfg.bot_aliases)

    cfg.chat_id, cfg.send_name = resolve_chat(cfg.target)
    log(cfg, "START", target=cfg.target, chat_id=cfg.chat_id, send_name=cfg.send_name,
        use_self=cfg.use_self, dry_run=cfg.dry_run, loop=args.loop, model=cfg.model,
        context_limit=cfg.context_limit, poll_seconds=cfg.poll_seconds,
        delay=f"{cfg.delay_min}-{cfg.delay_max}", min_gap=cfg.min_gap,
        max_per_hour=cfg.max_per_hour, bot_name=cfg.bot_name, bot_aliases=cfg.bot_aliases)

    state = load_state(cfg)

    if not args.loop:
        try:
            run_cycle(client, cfg, system_prompt, state)
        except Exception as e:
            log(cfg, "ERROR", msg=str(e))
        return

    while True:
        try:
            if run_cycle(client, cfg, system_prompt, state) == "stop":
                break
        except Exception as e:
            log(cfg, "ERROR", msg=str(e))
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
