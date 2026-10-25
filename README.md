# 플레이리스트 추천기

내 플레이리스트의 곡 목록을 붙여넣거나 YouTube·Spotify 링크를 넣으면, 어울리는 곡을 **추천 이유**와 함께 보여주는 웹앱입니다.

버전별 업데이트 사항은 [CHANGELOG.md](CHANGELOG.md)와 [Releases](https://github.com/RabbitHaru/playlist-recommender/releases)에서 볼 수 있어요.

## 동작 방식
1. 곡 목록을 붙여넣거나(로그인 불필요), YouTube(Music) 공개 플레이리스트 링크를 넣음(`YOUTUBE_API_KEY` 필요, 로그인 불필요). Spotify는 로그인(PKCE, 읽기 권한만) 후 내가 만들었거나 함께 편집하는 플레이리스트의 곡 목록을 읽음. 비밀번호는 Spotify 페이지에서만 입력하고 앱은 받지 않음
2. 곡마다 Last.fm `track.getSimilar`로 비슷한 곡을 받아 점수를 합산 (여러 곡에서 함께 나올수록 높은 점수)
3. **추천 성향**: 익숙하게(여러 곡과 겹치는 안전한 곡) / 균형 있게 / 새롭게(덜 알려진 곡·새 아티스트 우선)
3-1. 곡마다 2줄짜리 추천 이유(가장 비슷한 곡·유사도, 겹치는 장르 태그, 아티스트, 청취자 수)를 표시
4. 이미 플레이리스트에 있는 곡, 이전에 추천한 곡은 제외 (추천 기록은 브라우저 localStorage에만 저장)
5. 마음에 드는 곡은 ‘＋ 플레이리스트에 추가’(또는 ‘모두 추가’)로 바로 추가. 로그인은 읽기 권한만으로 시작하고, **‘추가 권한 허용’을 눌렀을 때만** 플레이리스트 수정 권한(`playlist-modify-*`)을 따로 요청

## 실행
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # 키를 채워 넣기
.venv\Scripts\python -m uvicorn app.main:app --reload
```
http://127.0.0.1:8000 접속 (`localhost`가 아니라 `127.0.0.1`). Spotify 대시보드에 Redirect URI `http://127.0.0.1:8000/callback`을 등록해야 로그인이 돼요. 테스트: `.venv\Scripts\python -m unittest discover -s tests`

## 품질 확인 (v0.2.1 기준)
`scripts/evaluate.py`가 장르별 인기곡 60개를 가짜 플레이리스트로 쓰고 20%를 숨긴 채 30곡을 추천해서, 숨긴 곡이 다시 나오는지 봐요.
- 숨긴 곡 재현율 17~50% (같은 수를 무작위로 고르면 0~8%), 추천 곡의 장르 일치율 87~100%
- 한 번에 30곡이 모두 채워졌어요 (indie rock, k-pop, korean, hip-hop, jazz, electronic)
- 한계: 가짜 플레이리스트가 추천에 쓰는 Last.fm 데이터와 같은 출처라서 수치가 실제보다 후하게 나와요. 회귀 확인용이지 절대 점수가 아니에요.

## 알려진 한계
- Spotify 개발 모드 앱은 로그인한 본인 소유(또는 협업) 플레이리스트의 곡 목록만 읽을 수 있어서, 남의 공개 플레이리스트는 링크만으로 읽을 수 없음 (곡 목록 직접 붙여넣기는 가능)
- Last.fm 데이터 기반이라 인기곡 쏠림이 있고, 한국 음악 등 데이터가 적은 곡은 추천이 부실할 수 있음
- 서버를 재시작하면 로그인이 풀림 (세션이 메모리에만 있음)

## 구조
- `app/providers/` 플랫폼 어댑터. 새 플랫폼은 `SpotifyProvider`와 같은 멤버(`can_handle`, `fetch_playlist`, `find_link`)를 가진 클래스를 추가하고 `get_providers()`에 등록
- `app/lastfm.py` 비슷한 곡 데이터 (6시간 캐시)
- `app/recommender.py` 플랫폼과 무관한 추천 로직
- `app/main.py` API (IP당 분당 10회 제한, 서버는 사용자 데이터를 저장하지 않음)

## 공개 배포 전에 확인할 것 (약관)
- **Spotify**: 개발 모드 앱은 사용자 수 제한이 있어서 불특정 다수에게 공개하려면 Spotify의 확장 승인이 필요할 수 있음. Spotify 콘텐츠로 AI/ML 모델을 학습시키는 것은 금지. 곡 링크와 Spotify 표기는 유지.
- **Last.fm**: 무료 API는 비상업적 이용이 기본. 상업적 이용은 별도 허가가 필요하고, Last.fm 출처 표기와 캐시 정책을 지켜야 함.
- **YouTube Music**: 비공식 라이브러리(ytmusicapi)는 약관 위반 소지가 있어 쓰지 않음. 추가한다면 공식 YouTube Data API(무료 일일 쿼터)를 사용.
- 약관은 바뀔 수 있으니 배포 직전에 각 서비스의 최신 약관을 다시 확인할 것.

## 다음 단계
- [x] Spotify 로그인(OAuth PKCE)
- [x] 추천 곡을 플레이리스트에 바로 추가 (쓰기 권한은 선택적으로 요청)
- [ ] 무료 오픈 데이터(ListenBrainz/MusicBrainz)를 두 번째 추천 소스로 추가
- [ ] YouTube 어댑터 (공식 Data API)
