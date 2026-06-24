#!/usr/bin/env python3
"""
카톡 단톡방 자율 응답 봇 (테스트용) — 멀티 톡방 / 설정 외부화 버전

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

# 내장 기본값
DEFAULTS = {
    "TARGET": "내톡방",
    "MODEL": "claude-opus-4-8",
    "DRY_RUN": True,        # 안전 기본값. 실제 전송하려면 false 로.
    "USE_SELF": False,      # true면 전송을 자기채팅으로 (읽기는 TARGET)
    "CONTEXT_LIMIT": 40,    # LLM에 넘기는 최근 맥락 메시지 수
    "POLL_SECONDS": 10,     # 루프 폴링 주기(초)
    "DELAY_MIN": 3,         # 응답 전 랜덤 대기 하한(초)
    "DELAY_MAX": 9,         # 상한
    "MIN_GAP": 5,           # 내 마지막 발화 후 최소 경과(초)
    "MAX_PER_HOUR": 0,      # 시간당 최대 발화 수 (0=무제한)
    "FETCH_LIMIT": 80,      # 한 번에 읽어올 메시지 수
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
    if os.path.exists(cfg.state_path):
        with open(cfg.state_path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_processed_id": 0,
        "responded_ids": [],
        "sent_ids": [],
        "last_sent_ts": None,
        "sent_timestamps": [],
    }


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
                partial = (cid, target)
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


def build_system_prompt(style, examples):
    return f"""너는 카톡 단톡방에서 "신민석"의 말투로 대신 답하는 봇이다.
아래 STYLE.md는 신민석의 관찰된 말투 규칙이고, examples.txt는 실제 대화 예시다.
이 톤을 그대로 흉내 내되, 자연스럽고 사람처럼 행동하라.

[가장 중요한 규칙]
- 대부분의 순간에는 "응답하지 않는다"가 정답이다. 침묵을 적극적인 선택지로 삼아라.
- 다음 중 하나일 때만 응답을 고려한다:
  (1) 내 이름/별명(민석, 민석이 등)이 언급됨
  (2) 단톡방에 던져진 질문에 내가 답할 만함
  (3) 흐름상 가벼운 리액션이 자연스럽고, 끼어드는 게 어색하지 않은 순간
- 평소엔 말수가 적지만, 일단 대화에 끼면 티키타카로 짧게 여러 번 주고받는 게 자연스럽다.
  내가 방금 말했더라도 상대가 받아치거나 대화가 이어지는 중이면 계속 말해도 된다.
- 단, 아무도 반응 안 하는데 혼자 연달아 떠드는 독백/도배는 하지 않는다.
- 애매하면 침묵한다.

[응답할 때의 말투]
- STYLE.md 규칙을 따른다. 짧게, 반말, ㅋ/ㅎ/이모지 거의 안 씀, 느낌표 자제.
- 한 호흡 넘으면 짧은 메시지 2~3개로 쪼갠다.

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
    convo_lines = []
    for m in context_msgs:
        who = "나(신민석)" if m.get("is_from_me") else m.get("sender", "?")
        convo_lines.append(f"[{who}] {m.get('text','')}")
    convo = "\n".join(convo_lines)
    user_content = (
        "아래는 단톡방의 최근 대화다. 맨 아래가 가장 최신 메시지.\n"
        "내가(신민석) 지금 끼어들어 말하는 게 자연스러운지 판단하고, "
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

    delay = random.randint(cfg.delay_min, cfg.delay_max)
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
            after = fetch_messages(cfg.chat_id, 20)
            for m in after:
                if m.get("is_from_me") and m["id"] not in state["sent_ids"]:
                    state["sent_ids"].append(m["id"])
            state["sent_ids"] = state["sent_ids"][-200:]
        except Exception as e:
            log(cfg, "WARN", msg=f"전송 후 재조회 실패: {e}")

    state["last_processed_id"] = max(newest_id, max(state["sent_ids"], default=newest_id))
    save_state(cfg, state)
    return "ok"


def build_arg_parser():
    p = argparse.ArgumentParser(description="카톡 단톡방 자율 응답 봇")
    p.add_argument("--target", help="톡방 이름(부분일치) 또는 chat-id")
    p.add_argument("--model")
    p.add_argument("--context-limit", dest="context_limit", type=int, help="LLM 맥락 메시지 수")
    p.add_argument("--poll-seconds", dest="poll_seconds", type=int, help="루프 폴링 주기(초)")
    p.add_argument("--delay-min", dest="delay_min", type=int, help="응답 전 랜덤 대기 하한(초)")
    p.add_argument("--delay-max", dest="delay_max", type=int, help="응답 전 랜덤 대기 상한(초)")
    p.add_argument("--min-gap", dest="min_gap", type=int, help="내 마지막 발화 후 최소 간격(초)")
    p.add_argument("--max-per-hour", dest="max_per_hour", type=int, help="시간당 최대 발화(0=무제한)")
    p.add_argument("--fetch-limit", dest="fetch_limit", type=int, help="한 번에 읽는 메시지 수")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                   help="실제 전송 안 함(초안만)")
    p.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                   help="실제 전송함")
    p.add_argument("--use-self", dest="use_self", action="store_true", default=None,
                   help="전송을 자기채팅으로(읽기는 TARGET)")
    p.add_argument("--loop", action="store_true", help="지속 가동(폴링 루프)")
    return p


def main():
    args = build_arg_parser().parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 가 필요합니다 (.env 또는 환경변수).", file=sys.stderr)
        sys.exit(1)

    cfg = Config(args)

    import anthropic
    client = anthropic.Anthropic(max_retries=4)  # 일시적 과부하(529)·네트워크 오류 자동 재시도

    style = read_text_file(cfg.style_path)
    examples = read_text_file(cfg.examples_path)
    if not style:
        print(f"WARN: {cfg.style_path} 가 없음. update_style.py 로 먼저 생성하세요.", file=sys.stderr)
    system_prompt = build_system_prompt(style, examples)

    cfg.chat_id, cfg.send_name = resolve_chat(cfg.target)
    log(cfg, "START", target=cfg.target, chat_id=cfg.chat_id, send_name=cfg.send_name,
        use_self=cfg.use_self, dry_run=cfg.dry_run, loop=args.loop, model=cfg.model,
        context_limit=cfg.context_limit, poll_seconds=cfg.poll_seconds,
        delay=f"{cfg.delay_min}-{cfg.delay_max}", min_gap=cfg.min_gap,
        max_per_hour=cfg.max_per_hour)

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
    main()
