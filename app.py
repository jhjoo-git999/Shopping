"""쇼핑 리스트 Streamlit 앱."""

import html
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st

DATA_FILE = Path(__file__).parent / "shopping_list.json"
TMP_FILE = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
NAME_MAX_LEN = 50
QTY_MIN = 1
QTY_MAX = 999
EMPTY_MESSAGE = "목록이 비어 있어요. 위에서 아이템을 추가하세요."
MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!$~|>])")


# ---------- 저장소 ----------

@st.cache_resource
def file_lock() -> threading.RLock:
    """모든 세션(탭)이 공유하는 파일 잠금. 스크립트가 매번 다시 실행돼도 같은 객체를 쓴다."""
    return threading.RLock()


def is_valid_item(item) -> bool:
    """저장 파일의 아이템 하나가 PRD 6장 데이터 모델에 맞는지 확인한다."""
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("name"), str)
        and type(item.get("quantity")) is int  # bool은 int의 하위 클래스라 isinstance로는 못 거른다
        and isinstance(item.get("checked"), bool)
        and isinstance(item.get("created_at"), str)
    )


def load_items() -> tuple[list[dict], str | None]:
    """저장 파일에서 목록을 읽는다. 깨진 파일은 시각을 붙인 .bak으로 옮기고 빈 목록과 경고를 돌려준다."""
    with file_lock():
        if not DATA_FILE.exists():
            return [], None
        try:
            with DATA_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not all(is_valid_item(i) for i in data):
                raise ValueError("목록 형식이 아님")
            return data, None
        except (OSError, ValueError):  # json.JSONDecodeError는 ValueError의 하위 클래스
            backup = DATA_FILE.with_name(f"{DATA_FILE.name}.{datetime.now():%Y%m%d-%H%M%S}.bak")
            try:
                DATA_FILE.replace(backup)
            except OSError:
                pass
            return [], f"저장 파일을 읽을 수 없어 빈 목록으로 시작해요. 기존 파일은 {backup.name}으로 옮겼어요."


def save_items(items: list[dict]) -> None:
    """아이템 목록을 저장 파일에 쓴다. 임시 파일에 쓴 뒤 바꿔치기해 반쯤 쓴 파일이 읽히지 않게 한다."""
    with file_lock():
        with TMP_FILE.open("w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(TMP_FILE, DATA_FILE)


def new_item(name: str, quantity: int) -> dict:
    """새 아이템 딕셔너리를 만든다."""
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------- 상태 ----------

def init_state() -> None:
    """세션 상태를 준비하고, 매 실행마다 파일의 최신 목록을 불러온다."""
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("editing_id", None)
    st.session_state.setdefault("form_nonce", 0)  # 추가 성공 시 1 증가 → 추가 폼 위젯을 새로 만든다
    refresh()


def notify(level: str, text: str) -> None:
    """다음 렌더링에서 보여줄 메시지를 쌓는다. level: warning / info / error / toast."""
    st.session_state.messages.append((level, text))


def refresh() -> None:
    """파일의 최신 목록으로 세션 상태를 맞춘다 (다른 탭·기기에서 바꾼 내용 반영)."""
    items, warning = load_items()
    st.session_state.shopping_items = items
    if warning:
        notify("warning", warning)


def mutate(change: Callable[[list[dict]], list[dict]]) -> bool:
    """파일의 최신 목록을 읽어 change로 바꾼 뒤 저장한다. 읽기-수정-쓰기를 한 잠금 안에서 처리한다."""
    with file_lock():
        refresh()
        items = change(st.session_state.shopping_items)
        st.session_state.shopping_items = items
        try:
            save_items(items)
        except OSError as e:
            notify("error", f"저장하지 못했어요: {e}")
            return False
    return True


def find_item(item_id: str) -> dict | None:
    """id로 아이템을 찾는다."""
    return next((i for i in st.session_state.shopping_items if i["id"] == item_id), None)


def validate(raw_name: str | None, quantity) -> tuple[str | None, str | None]:
    """이름·수량을 검증한다. (정리된 이름, 오류 메시지)를 돌려준다."""
    name = (raw_name or "").strip()
    if not name:
        return None, "아이템 이름을 입력하세요."
    if len(name) > NAME_MAX_LEN:
        return None, f"아이템 이름은 {NAME_MAX_LEN}자 이하로 입력하세요."
    if quantity is None or not QTY_MIN <= quantity <= QTY_MAX:
        return None, f"수량은 {QTY_MIN}~{QTY_MAX} 사이로 입력하세요."
    return name, None


def md_escape(text: str) -> str:
    """마크다운으로 해석되는 텍스트(토스트 등)에 사용자 입력을 넣을 때 서식·링크·수식이 적용되지 않게 한다."""
    # Streamlit은 마크다운 처리 뒤 URL·이메일처럼 보이는 글자(http://, www., a@b)를 따로 링크로 바꾼다.
    # 이스케이프로는 막을 수 없어 . : @ 앞뒤에 보이지 않는 WORD JOINER를 넣어 인식을 끊는다.
    text = re.sub(r"([.:@])", "⁠\\1⁠", html.escape(text, quote=False))
    return MD_SPECIAL.sub(r"\\\1", text)


# ---------- 콜백 ----------

def on_add() -> None:
    """추가 폼 제출을 처리한다."""
    nonce = st.session_state.form_nonce
    raw_qty = st.session_state[f"new_qty_{nonce}"]
    name, error = validate(st.session_state[f"new_name_{nonce}"], raw_qty)
    if error:
        notify("warning", error)  # 입력값은 그대로 남겨 고칠 수 있게 한다
        return

    def change(items: list[dict]) -> list[dict]:
        if any(i["name"].casefold() == name.casefold() for i in items):
            notify("info", "이미 목록에 있는 아이템이에요.")
        return items + [new_item(name, int(raw_qty))]

    if mutate(change):
        notify("toast", f"{md_escape(name)} 추가됨")
        # 위젯 key를 바꿔 입력칸을 새로 만든다. 콜백에서 값만 비우면 Enter로 제출한 뒤
        # 브라우저 폼이 이전 값을 들고 있다가 다음 제출 때 다시 보낸다.
        st.session_state.form_nonce += 1


def on_toggle(item_id: str) -> None:
    """체크 상태를 바꾼다."""
    checked = st.session_state[f"check_{item_id}"]
    found = False

    def change(items: list[dict]) -> list[dict]:
        nonlocal found
        for i in items:
            if i["id"] == item_id:
                i["checked"], found = checked, True
        return items

    mutate(change)
    if not found:
        notify("info", "다른 곳에서 이미 삭제된 아이템이에요.")


def on_start_edit(item_id: str) -> None:
    """해당 아이템을 편집 모드로 바꾼다."""
    item = find_item(item_id)
    if item:
        st.session_state.editing_id = item_id
        st.session_state[f"edit_name_{item_id}"] = item["name"]
        st.session_state[f"edit_qty_{item_id}"] = item["quantity"]


def on_save_edit(item_id: str) -> None:
    """편집 내용을 검증해 저장한다. 실패하면 편집 모드를 유지한다."""
    raw_qty = st.session_state[f"edit_qty_{item_id}"]
    name, error = validate(st.session_state[f"edit_name_{item_id}"], raw_qty)
    if error:
        notify("warning", error)
        return
    found = False

    def change(items: list[dict]) -> list[dict]:
        nonlocal found
        for i in items:
            if i["id"] == item_id:
                i["name"], i["quantity"], found = name, int(raw_qty), True
        return items

    mutate(change)
    st.session_state.editing_id = None
    if not found:
        notify("info", "다른 곳에서 이미 삭제된 아이템이에요.")


def on_cancel_edit() -> None:
    """편집을 취소한다."""
    st.session_state.editing_id = None


def on_delete(item_id: str) -> None:
    """아이템 하나를 삭제한다."""
    removed = []

    def change(items: list[dict]) -> list[dict]:
        removed.extend(i for i in items if i["id"] == item_id)
        return [i for i in items if i["id"] != item_id]

    mutate(change)
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None
    if removed:
        notify("toast", f"{md_escape(removed[0]['name'])} 삭제됨")


def on_clear_checked() -> None:
    """체크된 아이템을 모두 삭제한다."""
    removed_ids = set()

    def change(items: list[dict]) -> list[dict]:
        removed_ids.update(i["id"] for i in items if i["checked"])
        return [i for i in items if not i["checked"]]

    mutate(change)
    if st.session_state.editing_id in removed_ids:
        st.session_state.editing_id = None
    notify("toast", f"{len(removed_ids)}개 삭제됨")


# ---------- 화면 ----------

CSS = """
<style>
/* 목록 행 = st.container(key="item_list") 안의 가로 블록(st.columns) 하나.
   행마다 key를 단 컨테이너를 쓰면 빠른 연속 삭제 때 지운 행의 잔상이 남아서 목록 전체에만 key를 단다 */
.st-key-item_list [data-testid="stHorizontalBlock"] {
    border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    padding: 0.15rem 0;
}
.item-name { font-size: 1.05rem; word-break: break-word; }
.item-name.done { text-decoration: line-through; opacity: 0.5; }
.item-line { display: flex; align-items: baseline; justify-content: space-between; gap: 0.75rem; }
.item-qty { font-size: 0.85rem; opacity: 0.6; white-space: nowrap; }
.progress-text { margin: 0 0 0.25rem; opacity: 0.8; }
.all-done { margin: 0.25rem 0 0; font-weight: 600; }
.empty-state { text-align: center; padding: 2.5rem 0; opacity: 0.6; }
.empty-state .icon { font-size: 2.5rem; display: block; margin-bottom: 0.5rem; }
.st-key-add_row button p { white-space: nowrap; }

/* 모바일: Streamlit은 640px 이하에서 컬럼을 세로로 쌓으므로, 목록 행과 입력 폼만 가로 배치를 유지한다 */
@media (max-width: 640px) {
    .st-key-item_list [data-testid="stHorizontalBlock"],
    .st-key-add_row [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap;
        gap: 0.35rem;
    }
    .st-key-item_list [data-testid="stColumn"],
    .st-key-add_row [data-testid="stColumn"] {
        min-width: 0 !important;
    }
    /* 아이콘 버튼은 줄어들지 않게 고정 폭을 주고, 남는 폭은 이름 칸이 가져간다 */
    .st-key-item_list [data-testid="stColumn"]:has(button) {
        flex: 0 0 2.25rem !important;
        width: 2.25rem !important;
    }
    .st-key-item_list button {
        width: 2.25rem;
        min-width: 2.25rem;
        padding-left: 0;
        padding-right: 0;
    }
    .st-key-item_list [data-testid="stColumn"]:has([data-testid="stCheckbox"]) {
        flex: 0 0 1.75rem !important;
        width: 1.75rem !important;
    }
    .st-key-add_row [data-testid="stColumn"]:has(button) {
        flex: 0 0 4rem !important;
        width: 4rem !important;
    }
}
@media (max-width: 400px) {
    [data-testid="stHeading"] h1 { font-size: 1.9rem; }
}
</style>
"""


def inject_css() -> None:
    """앱 전체에 쓰는 CSS를 한 번에 넣는다."""
    st.markdown(CSS, unsafe_allow_html=True)


def render_header() -> None:
    """제목과 진행 상황 영역을 그린다."""
    st.title("🛒 쇼핑 리스트")
    items = st.session_state.shopping_items
    if not items:
        return
    done = sum(i["checked"] for i in items)
    st.markdown(f'<p class="progress-text">{done} / {len(items)}개 구매 완료</p>', unsafe_allow_html=True)
    st.progress(done / len(items))
    if done == len(items):
        st.markdown('<p class="all-done">장보기 완료! 🎉</p>', unsafe_allow_html=True)


def render_messages() -> None:
    """콜백에서 쌓인 메시지를 한 번 보여주고 비운다."""
    show = {"warning": st.warning, "info": st.info, "error": st.error, "toast": st.toast}
    for level, text in st.session_state.messages:
        show[level](text)
    st.session_state.messages = []


def render_add_form() -> None:
    """아이템 추가 폼 영역을 그린다."""
    nonce = st.session_state.form_nonce
    with st.form("add_form", border=False), st.container(key="add_row"):
        c_name, c_qty, c_btn = st.columns([6, 2, 2], vertical_alignment="bottom")
        c_name.text_input("아이템 이름", key=f"new_name_{nonce}", max_chars=NAME_MAX_LEN,
                          placeholder="아이템 이름 (예: 우유)", label_visibility="collapsed")
        # min/max를 주면 범위 밖 값은 브라우저가 서버로 보내지 않아 안내 없이 무시되므로, 범위는 validate()가 검사한다
        c_qty.number_input("수량", value=1, step=1, key=f"new_qty_{nonce}", label_visibility="collapsed")
        c_btn.form_submit_button("추가", on_click=on_add, key="add_submit", type="primary", width="stretch")


def render_item_row(item: dict) -> None:
    """아이템 한 줄을 그린다. 편집 중이면 입력칸과 저장/취소 버튼을 보여준다."""
    item_id = item["id"]
    if st.session_state.editing_id == item_id:
        c_name, c_qty, c_save, c_cancel = st.columns([6.8, 1.5, 0.8, 0.8], vertical_alignment="center")
        c_name.text_input("이름", key=f"edit_name_{item_id}", max_chars=NAME_MAX_LEN, label_visibility="collapsed")
        c_qty.number_input("수량", step=1, key=f"edit_qty_{item_id}", label_visibility="collapsed")
        c_save.button("💾", key=f"save_{item_id}", help="저장", type="primary",
                      on_click=on_save_edit, args=(item_id,))
        c_cancel.button("✖️", key=f"cancel_{item_id}", help="취소", on_click=on_cancel_edit)
        return

    # 다른 탭에서 바뀐 체크 상태도 보이도록 매번 파일 값으로 맞춘다 (그래서 value=는 넘기지 않는다)
    st.session_state[f"check_{item_id}"] = item["checked"]
    # 이름과 수량은 한 요소로 그린다 (200개일 때 다시 그릴 요소 수를 줄여 갱신을 빠르게)
    c_check, c_name, c_edit, c_del = st.columns([0.5, 7.5, 0.8, 0.8], vertical_alignment="center")
    c_check.checkbox(item["name"], key=f"check_{item_id}",
                     on_change=on_toggle, args=(item_id,), label_visibility="collapsed")
    done = " done" if item["checked"] else ""
    # st.markdown은 이름 속 **, $, [](...) 같은 마크다운을 해석하므로 st.html로 그린다
    c_name.html(f'<div class="item-line"><span class="item-name{done}">{html.escape(item["name"])}</span>'
                f'<span class="item-qty">x{item["quantity"]}</span></div>')
    c_edit.button("✏️", key=f"edit_{item_id}", help="수정", type="tertiary",
                  on_click=on_start_edit, args=(item_id,))
    c_del.button("🗑️", key=f"del_{item_id}", help="삭제", type="tertiary",
                 on_click=on_delete, args=(item_id,))


def render_item_list() -> None:
    """아이템 목록 영역을 그린다."""
    items = st.session_state.shopping_items
    if not items:
        st.markdown(f'<div class="empty-state"><span class="icon">🛒</span>{EMPTY_MESSAGE}</div>',
                    unsafe_allow_html=True)
        return
    with st.container(key="item_list"):
        for item in items:
            render_item_row(item)


def render_footer() -> None:
    """하단 일괄 삭제 영역을 그린다."""
    if not st.session_state.shopping_items:
        return
    has_checked = any(i["checked"] for i in st.session_state.shopping_items)
    st.write("")
    st.button("체크된 항목 모두 삭제", key="clear_checked", type="secondary",
              on_click=on_clear_checked, disabled=not has_checked)


def main() -> None:
    """앱 진입점."""
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    init_state()
    inject_css()
    render_header()
    render_messages()
    render_add_form()
    render_item_list()
    render_footer()


main()
