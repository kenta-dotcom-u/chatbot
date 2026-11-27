import json
import streamlit as st
from google import genai
from google.genai import types

# ページ設定
st.set_page_config(page_title="リアルタイム議事録メーカー (Gemini版)", page_icon="🎙️", layout="wide")
st.title("🎙️ リアルタイム議事録作成アプリ（Gemini版）")
st.caption("会議の区切りごとに音声を録音すると、文字起こしと議事録の更新を自動で行います。")

# セッション状態の初期化
if "transcript_history" not in st.session_state:
    st.session_state.transcript_history = []
if "minutes" not in st.session_state:
    st.session_state.minutes = "（まだ議事録はありません。音声を録音してください）"
if "last_audio_bytes" not in st.session_state:
    st.session_state.last_audio_bytes = None

# サイドバー：APIキー設定とリセット
with st.sidebar:
    st.header("⚙️ 設定")
    # Streamlit Secretsにキーがあれば自動読み込み、なければ手入力
    default_api_key = st.secrets.get("GEMINI_API_KEY", "")
    api_key = st.text_input("Gemini API Key", value=default_api_key, type="password")
    
    st.divider()
    if st.button("🗑️ 議事録と履歴をリセット", use_container_width=True):
        st.session_state.transcript_history = []
        st.session_state.minutes = "（まだ議事録はありません。音声を録音してください）"
        st.session_state.last_audio_bytes = None
        st.rerun()

# APIキーのチェック
if not api_key:
    st.warning("👈 左のサイドバーに Gemini API Key を入力してください。（Google AI Studioで取得できます）")
    st.stop()

# Geminiクライアントの初期化
client = genai.Client(api_key=api_key)

# メイン画面：音声入力エリア
st.subheader("1. 音声の録音")
audio_value = st.audio_input("マイクボタンを押して発言を録音（停止すると自動処理されます）")

# 新しい音声が録音された場合の処理
if audio_value is not None:
    audio_bytes = audio_value.getvalue()
    
    # 同じ音声データに対する重複実行を防止
    if audio_bytes != st.session_state.last_audio_bytes:
        st.session_state.last_audio_bytes = audio_bytes
        
        with st.spinner("Geminiが音声を解析して議事録を更新中..."):
            try:
                past_transcripts = "\n".join(st.session_state.transcript_history)
                
                prompt = f"""
あなたは優秀な書記です。
添付された【新しい音声データ】を正確に日本語で文字起こしし、さらに【これまでの発言履歴】と統合して最新の議事録を作成してください。

【これまでの発言履歴】
{past_transcripts if past_transcripts else "（なし・これが最初の発言です）"}

【議事録の出力フォーマット（Markdown形式）】
## 📌 決定事項
- 箇条書きで記載（なければ「特になし」）

## 📝 議論の要約
- 話題ごとに整理して記載

## 🚀 ネクストアクション（TODO）
- 担当者や期限がわかる場合は記載（なければ「特になし」）
"""
                # Gemini 2.5 Flash に音声バイトデータとプロンプトを同時送信
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        types.Part.from_bytes(
                            data=audio_bytes,
                            mime_type="audio/wav",
                        ),
                        prompt,
                    ],
                    # 文字起こし結果と議事録を確実に分離するためJSON形式で出力させる
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema={
                            "type": "OBJECT",
                            "properties": {
                                "new_transcript": {
                                    "type": "STRING",
                                    "description": "今回添付された音声データのみの正確な日本語文字起こし",
                                },
                                "updated_minutes": {
                                    "type": "STRING",
                                    "description": "これまでの履歴と今回の内容を統合した最新の議事録（Markdown形式）",
                                },
                            },
                            "required": ["new_transcript", "updated_minutes"],
                        },
                        temperature=0.2,
                    ),
                )
                
                # 結果をパースして画面状態を更新
                result = json.loads(response.text)
                st.session_state.transcript_history.append(result["new_transcript"])
                st.session_state.minutes = result["updated_minutes"]
                st.success("議事録を更新しました！")
                
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

st.divider()

# 結果表示エリア（2カラム構成）
col1, col2 = st.columns([3, 2])

with col1:
    st.subheader("2. 生成された議事録（リアルタイム更新）")
    st.markdown(st.session_state.minutes)
    
    st.download_button(
        label="📥 議事録をMarkdownでダウンロード",
        data=st.session_state.minutes,
        file_name="meeting_minutes.md",
        mime="text/markdown"
    )

with col2:
    st.subheader("3. 文字起こしログ（時系列）")
    if st.session_state.transcript_history:
        for i, text in enumerate(st.session_state.transcript_history, 1):
            st.info(f"**発言 {i}:** {text}")
    else:
        st.write("録音されたテキストがここに蓄積されます。")
