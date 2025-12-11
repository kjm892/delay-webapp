#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 스마트스토어 발송지연 처리 웹앱 (Streamlit Cloud 배포용)
"""

import os
import time
import json
import base64
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
import requests
import gspread
import bcrypt
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials

# ===================== 설정 =====================
API_HOST = "https://api.commerce.naver.com"
TOKEN_URL = f"{API_HOST}/external/v1/oauth2/token"
REQUEST_TIMEOUT = 30
MAX_WORKERS = 20
STORES_SHEET = "마켓정보"
# ================================================

# ========== 페이지 설정 ==========
st.set_page_config(
    page_title="발송지연 처리",
    page_icon="📦",
    layout="centered"
)

# ========== CSS 스타일 ==========
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        color: #03c75a;
        margin-bottom: 1rem;
    }
    .market-info {
        background-color: #f0f9f4;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
    .success-box {
        background-color: #d4edda;
        border-left: 4px solid #28a745;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .error-box {
        background-color: #f8d7da;
        border-left: 4px solid #dc3545;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .stButton>button {
        background-color: #03c75a;
        color: white;
        font-weight: bold;
        width: 100%;
        padding: 0.75rem;
        border: none;
        border-radius: 8px;
    }
    .stButton>button:hover {
        background-color: #02a84d;
    }
</style>
""", unsafe_allow_html=True)


# ========== 유틸 함수 ==========
def S(v: Any) -> str:
    try:
        return "" if v is None else str(v).strip()
    except:
        return ""


# ========== 설정 로드 ==========
def get_config():
    """설정 정보 가져오기 (Streamlit Secrets 또는 세션)"""
    config = {}

    # Streamlit Secrets에서 가져오기 (Cloud 배포용)
    try:
        # gcp_service_account 섹션 확인
        if hasattr(st, 'secrets') and "gcp_service_account" in st.secrets:
            config["credentials"] = dict(st.secrets["gcp_service_account"])

        # spreadsheet_key 확인
        if hasattr(st, 'secrets') and "spreadsheet_key" in st.secrets:
            config["spreadsheet_key"] = st.secrets["spreadsheet_key"]
    except Exception:
        pass

    # 세션에서 가져오기 (사용자 입력 - 로컬 실행용)
    if "credentials" not in config and "user_credentials" in st.session_state:
        config["credentials"] = st.session_state["user_credentials"]
    if "spreadsheet_key" not in config and "user_spreadsheet_key" in st.session_state:
        config["spreadsheet_key"] = st.session_state["user_spreadsheet_key"]

    return config


def show_settings_page():
    """설정 페이지 표시"""
    st.markdown('<p class="main-header">⚙️ 초기 설정</p>', unsafe_allow_html=True)

    st.info("""
    **처음 사용 시 설정이 필요합니다:**
    1. Google Cloud에서 서비스 계정 생성
    2. Google Sheets API 활성화
    3. credentials.json 다운로드
    4. 스프레드시트에 서비스 계정 이메일 공유
    """)

    with st.expander("📋 상세 설정 가이드", expanded=True):
        st.markdown("""
        ### 1단계: Google Cloud 서비스 계정 생성
        1. [Google Cloud Console](https://console.cloud.google.com) 접속
        2. 새 프로젝트 생성 또는 기존 프로젝트 선택
        3. **API 및 서비스 > 라이브러리**에서 "Google Sheets API" 활성화
        4. **API 및 서비스 > 사용자 인증 정보**에서 "서비스 계정" 생성
        5. 생성된 서비스 계정에서 **키 > 새 키 만들기 > JSON** 다운로드

        ### 2단계: 스프레드시트 설정
        1. 마켓 정보가 담긴 Google 스프레드시트 생성
        2. 첫 번째 시트 이름을 "마켓정보"로 설정
        3. 헤더: `마켓명 | client_id | client_secret`
        4. 서비스 계정 이메일(xxx@xxx.iam.gserviceaccount.com)에 스프레드시트 공유 (편집자 권한)
        5. 스프레드시트 URL에서 키 복사: `https://docs.google.com/spreadsheets/d/`**여기가키**`/edit`
        """)

    st.divider()

    # credentials.json 업로드
    st.subheader("1. credentials.json 업로드")
    uploaded_file = st.file_uploader(
        "Google Cloud 서비스 계정 JSON 파일",
        type=["json"],
        help="Google Cloud Console에서 다운로드한 서비스 계정 키 파일"
    )

    if uploaded_file:
        try:
            credentials = json.load(uploaded_file)
            st.session_state["user_credentials"] = credentials
            st.success(f"✅ 서비스 계정: {credentials.get('client_email', 'Unknown')}")
        except Exception as e:
            st.error(f"❌ JSON 파일 파싱 오류: {e}")

    # 스프레드시트 키 입력
    st.subheader("2. 스프레드시트 키 입력")
    spreadsheet_key = st.text_input(
        "Google 스프레드시트 키",
        value=st.session_state.get("user_spreadsheet_key", ""),
        help="스프레드시트 URL의 /d/와 /edit 사이의 문자열",
        placeholder="예: 1eod1Z8sj3nWLmnlsGNP5Hb8gO9NKMaEPCY9rqE09TaI"
    )

    if spreadsheet_key:
        st.session_state["user_spreadsheet_key"] = spreadsheet_key

    # 설정 완료 버튼
    if st.button("🚀 설정 완료", type="primary"):
        config = get_config()
        if "credentials" in config and "spreadsheet_key" in config:
            st.session_state["config_complete"] = True
            st.rerun()
        else:
            st.error("❌ credentials.json과 스프레드시트 키를 모두 입력해주세요.")


# ========== 인증 ==========
def sign_client_secret(client_id: str, client_secret: str, ts_ms: int) -> str:
    pwd = f"{client_id}_{ts_ms}".encode("utf-8")
    hashed = bcrypt.hashpw(pwd, client_secret.strip().encode("utf-8"))
    return base64.b64encode(hashed).decode("utf-8")


def get_access_token(client_id: str, client_secret: str) -> Optional[str]:
    ts = int(time.time() * 1000)
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "timestamp": ts,
        "client_secret_sign": sign_client_secret(client_id, client_secret, ts),
        "type": "SELF",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    try:
        r = requests.post(TOKEN_URL, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json().get("access_token")
    except:
        pass
    return None


# ========== 구글 시트 ==========
def load_markets(config: Dict) -> List[Dict]:
    """마켓 정보 로드"""
    credentials = config.get("credentials")
    spreadsheet_key = config.get("spreadsheet_key")

    if not credentials or not spreadsheet_key:
        raise ValueError("설정이 완료되지 않았습니다.")

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials, scope)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(spreadsheet_key)

    try:
        ws = sh.worksheet(STORES_SHEET)
    except:
        ws = sh.get_worksheet(0)

    values = ws.get_all_values()
    markets = []

    for i, row in enumerate(values[1:], start=2):
        if len(row) >= 3:
            store_name = S(row[0])
            client_id = S(row[1])
            client_secret = S(row[2])

            if store_name and client_id and client_secret:
                markets.append({
                    "store_name": store_name,
                    "client_id": client_id,
                    "client_secret": client_secret,
                })

    return markets


# ========== API 호출 ==========
def check_order_in_market(market: Dict, product_order_id: str) -> Optional[Dict]:
    """특정 마켓에서 상품주문번호 조회"""
    token = get_access_token(market["client_id"], market["client_secret"])
    if not token:
        return None

    url = f"{API_HOST}/external/v1/pay-order/seller/product-orders/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    payload = {"productOrderIds": [product_order_id]}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            response = r.json()
            data = response.get("data", [])
            if data and len(data) > 0:
                return {
                    "market": market,
                    "token": token,
                    "order_data": response
                }
    except:
        pass
    return None


def find_order_parallel(markets: List[Dict], product_order_id: str) -> Optional[Dict]:
    """병렬로 모든 마켓에서 주문 찾기"""
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(check_order_in_market, market, product_order_id): market
            for market in markets
        }

        for future in as_completed(futures):
            result = future.result()
            if result:
                return result
    return None


def get_delay_reason_code(delay_reason: str) -> str:
    """지연 사유를 API enum 코드로 변환

    유효한 enum 값:
    - CUSTOM_BUILD: 주문제작
    - RESERVED_DISPATCH: 예약발송
    - ETC: 기타
    - PRODUCT_PREPARE: 상품준비
    - OVERSEA_DELIVERY: 해외배송
    - CUSTOMER_REQUEST: 고객요청
    """
    if "해외" in delay_reason or "현지" in delay_reason or "배송중" in delay_reason:
        return "OVERSEA_DELIVERY"
    elif "주문제작" in delay_reason or "제작" in delay_reason:
        return "CUSTOM_BUILD"
    elif "예약" in delay_reason:
        return "RESERVED_DISPATCH"
    elif "고객" in delay_reason or "구매자" in delay_reason or "요청" in delay_reason:
        return "CUSTOMER_REQUEST"
    elif "상품" in delay_reason or "준비" in delay_reason or "재고" in delay_reason:
        return "PRODUCT_PREPARE"
    else:
        return "ETC"


def execute_delay_dispatch(token: str, product_order_id: str,
                           dispatch_due_date: str, delay_reason: str) -> Dict:
    """발송지연 처리 API 호출"""

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    iso_date = f"{dispatch_due_date}T23:59:59.000+09:00"
    reason_code = get_delay_reason_code(delay_reason)

    url = f"{API_HOST}/external/v1/pay-order/seller/product-orders/{product_order_id}/delay"

    payload = {
        "dispatchDueDate": iso_date,
        "delayedDispatchReason": reason_code,
        "dispatchDelayedDetailedReason": delay_reason
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return {"success": True, "message": f"발송지연 처리 완료 ({reason_code})"}
        else:
            try:
                error_data = r.json()
                error_msg = error_data.get("message", r.text)
            except:
                error_msg = r.text
            return {"success": False, "message": f"API 오류 ({r.status_code}): {error_msg[:150]}"}
    except Exception as e:
        return {"success": False, "message": f"요청 오류: {str(e)}"}


# ========== 메인 앱 ==========
def main_app():
    """메인 발송지연 처리 앱"""
    st.markdown('<p class="main-header">📦 발송지연 처리</p>', unsafe_allow_html=True)

    config = get_config()

    # 설정 초기화 버튼
    with st.sidebar:
        st.subheader("설정")
        if st.button("⚙️ 설정 변경"):
            st.session_state["config_complete"] = False
            if "user_credentials" in st.session_state:
                del st.session_state["user_credentials"]
            if "user_spreadsheet_key" in st.session_state:
                del st.session_state["user_spreadsheet_key"]
            st.rerun()

    # 마켓 정보 로드
    try:
        markets = load_markets(config)
        st.markdown(f'''
        <div class="market-info">
            ✅ 연동된 마켓: <strong>{len(markets)}개</strong>
        </div>
        ''', unsafe_allow_html=True)
    except Exception as e:
        st.error(f"❌ 마켓 정보 로드 실패: {e}")
        if st.button("설정 다시하기"):
            st.session_state["config_complete"] = False
            st.rerun()
        return

    # 입력 폼
    st.subheader("상품주문번호")
    order_input = st.text_area(
        "상품주문번호 입력",
        placeholder="상품주문번호를 입력하세요\n여러 개는 줄바꿈 또는 쉼표로 구분",
        height=120,
        label_visibility="collapsed"
    )
    st.caption("예: 2025121188249131, 2025121186293861")

    col1, col2 = st.columns(2)

    with col1:
        default_date = datetime.now() + timedelta(days=7)
        dispatch_date = st.date_input(
            "발송예정일",
            value=default_date,
            min_value=datetime.now().date()
        )

    with col2:
        delay_reason = st.selectbox(
            "지연 사유",
            options=[
                "배송중입니다",
                "해외배송으로 인한 지연",
                "현지 배송 중입니다",
                "상품준비 중",
                "주문제작으로 인한 지연",
                "예약발송",
                "고객요청으로 인한 지연",
                "기타 사유"
            ]
        )

    custom_reason = st.text_input("상세 사유 (선택)", placeholder="추가 설명을 입력하세요")

    # 처리 버튼
    if st.button("🚀 발송지연 처리", type="primary"):
        order_ids = [
            S(x) for x in order_input.replace(",", "\n").split("\n") if S(x)
        ]

        if not order_ids:
            st.warning("⚠️ 상품주문번호를 입력해주세요.")
            return

        final_reason = custom_reason if custom_reason else delay_reason
        dispatch_date_str = dispatch_date.strftime("%Y-%m-%d")

        st.divider()
        st.subheader("처리 결과")

        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()

        start_time = time.time()

        for i, order_id in enumerate(order_ids):
            status_text.text(f"처리 중... ({i+1}/{len(order_ids)}) - {order_id}")
            progress_bar.progress((i) / len(order_ids))

            found = find_order_parallel(markets, order_id)

            if not found:
                results.append({
                    "상품주문번호": order_id,
                    "마켓": "-",
                    "결과": "❌ 실패",
                    "메시지": "해당 상품주문번호를 찾을 수 없습니다."
                })
                continue

            delay_result = execute_delay_dispatch(
                found["token"],
                order_id,
                dispatch_date_str,
                final_reason
            )

            results.append({
                "상품주문번호": order_id,
                "마켓": found["market"]["store_name"],
                "결과": "✅ 성공" if delay_result["success"] else "❌ 실패",
                "메시지": delay_result["message"]
            })

        progress_bar.progress(1.0)
        total_time = time.time() - start_time
        status_text.text(f"완료! (소요시간: {total_time:.1f}초)")

        success_count = sum(1 for r in results if "✅" in r["결과"])
        fail_count = len(results) - success_count

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("총 처리", f"{len(results)}건")
        col2.metric("성공", f"{success_count}건")
        col3.metric("실패", f"{fail_count}건")
        col4.metric("소요시간", f"{total_time:.1f}초")

        st.dataframe(
            pd.DataFrame(results),
            use_container_width=True,
            hide_index=True
        )

        for r in results:
            if "✅" in r["결과"]:
                st.markdown(f'''
                <div class="success-box">
                    <strong>{r["상품주문번호"]}</strong> ({r["마켓"]})<br>
                    {r["메시지"]}
                </div>
                ''', unsafe_allow_html=True)
            else:
                st.markdown(f'''
                <div class="error-box">
                    <strong>{r["상품주문번호"]}</strong> ({r["마켓"]})<br>
                    {r["메시지"]}
                </div>
                ''', unsafe_allow_html=True)


def main():
    """메인 함수"""
    config = get_config()

    # 설정이 완료되었는지 확인
    has_credentials = "credentials" in config
    has_spreadsheet = "spreadsheet_key" in config

    # Secrets에서 설정이 로드되었으면 자동으로 메인 앱으로 이동
    if has_credentials and has_spreadsheet:
        main_app()
    elif st.session_state.get("config_complete", False):
        main_app()
    else:
        show_settings_page()


if __name__ == "__main__":
    main()
