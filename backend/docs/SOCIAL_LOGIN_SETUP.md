# 소셜 로그인(카카오/구글) 연결 가이드

코드는 전부 준비되어 있다(`POST /api/v1/auth/oauth-sync`, 프론트 `/auth/callback`).
**실제로 동작하려면 이 문서의 설정을 Kakao/Google 개발자 콘솔과 Supabase 대시보드에서
해야 한다** — API 키만으로는 할 수 없는 작업이라 팀원이 직접 진행해야 한다.

## 왜 필요한가

소셜 로그인은 이 프로젝트가 아니라 Supabase Auth(GoTrue)가 전담한다. 프론트가
`{SUPABASE_URL}/auth/v1/authorize?provider=kakao`로 브라우저를 보내면, Supabase가
카카오/구글과 직접 OAuth 핸드셰이크를 한다 — 그 핸드셰이크가 성립하려면 카카오/구글
쪽에 "이 앱이 우리 로그인을 써도 된다"고 등록된 Client ID/Secret이 있어야 하고, 그
값을 Supabase가 알고 있어야 한다.

## 1. Kakao 설정

1. [Kakao Developers](https://developers.kakao.com)에서 애플리케이션 생성(이미 있으면
   재사용).
2. **카카오 로그인** 활성화 (좌측 메뉴 → 제품 설정 → 카카오 로그인 → 활성화 설정 ON).
3. **Redirect URI** 등록 — Kakao 콘솔이 요구하는 이 값은 **우리 프론트 URL이 아니라
   Supabase의 고정 콜백 URL**이다:
   ```
   https://euzmnstknihfumpgdahx.supabase.co/auth/v1/callback
   ```
4. **동의 항목** 설정 (제품 설정 → 카카오 로그인 → 동의 항목): 최소 **닉네임**,
   **이메일**을 선택하고 "필수 동의"로 설정할 것을 권장한다. 이메일을 선택 동의로
   두면 사용자가 거부할 수 있는데, 이 경우 `oauth-sync`가 받는 `email`이 비어
   있을 수 있다(코드는 이 경우도 죽지 않도록 처리해뒀지만, 이메일이 없는 계정은
   추후 이메일 기반 기능이 애매해진다).
5. **REST API 키**(앱 키 → REST API 키)를 `Client ID`로, **Client Secret**(보안 →
   Client Secret 코드 생성, 활성화 상태 ON)을 `Client Secret`으로 확보.
6. Supabase 대시보드 → **Authentication → Providers → Kakao** → Enable 토글 ON →
   위 Client ID/Secret 입력 → Save.

## 2. Google 설정

1. [Google Cloud Console](https://console.cloud.google.com) → 프로젝트 선택(또는 생성)
   → **API 및 서비스 → OAuth 동의 화면**에서 앱 정보(앱 이름, 지원 이메일 등) 설정.
   테스트 단계라면 "테스팅" 상태로 두고 테스트 사용자를 추가해도 된다.
2. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트
   ID** → 애플리케이션 유형: **웹 애플리케이션**.
3. **승인된 리디렉션 URI**에 역시 Supabase의 고정 콜백 URL을 등록:
   ```
   https://euzmnstknihfumpgdahx.supabase.co/auth/v1/callback
   ```
4. 생성된 **클라이언트 ID**와 **클라이언트 보안 비밀번호** 확보.
5. Supabase 대시보드 → **Authentication → Providers → Google** → Enable 토글 ON →
   위 클라이언트 ID/보안 비밀번호 입력 → Save.

## 3. Supabase 쪽 리다이렉트 허용 목록 (두 제공자 공통, 반드시 필요)

Kakao/Google 설정과 별개로, Supabase는 로그인 성공 후 **우리 프론트로 돌아올 URL도
화이트리스트로 관리한다.** 이걸 안 하면 로그인은 성공하는데 마지막에 "요청한
리다이렉트 URL이 허용되지 않았습니다" 같은 오류로 막힌다.

Supabase 대시보드 → **Authentication → URL Configuration → Redirect URLs**에 추가:

```
http://localhost:3000/auth/callback   (로컬 개발)
https://<실제 배포 도메인>/auth/callback   (운영 배포 시 추가)
```

## 4. 프론트 환경변수

`frontend/.env.local`(신규 생성 필요, `.env.example`을 복사):

```
NEXT_PUBLIC_SUPABASE_URL=https://euzmnstknihfumpgdahx.supabase.co
```

이 값은 브라우저에 그대로 노출되는 공개 URL이라 민감정보가 아니다(ANON_KEY/
SERVICE_ROLE_KEY와는 다름 — 그것들은 절대 프론트에 넣지 말 것).

## 5. 동작 확인 방법

위 설정을 마친 뒤:

```
cd frontend
npm run dev
```

브라우저에서 `http://localhost:3000` → "회원가입" 탭 → **"카카오로 시작하기"** 또는
**"Google로 시작하기"** 클릭 → 실제 로그인 화면으로 이동해야 한다. 로그인 성공 후:

- **최초 로그인**이면 `/onboarding`으로 이동하고, 이름은 이미 알고 있으니 다시 안
  묻고 태어난 해부터 시작한다(`app/onboarding/page.tsx`가 `oauthPendingProfile`을
  보고 이 모드로 자동 전환됨). 생년/고향 입력 + 동의 체크 후 "동의하고 시작하기"를
  누르면 `PATCH /users/{id}` + 동의 기록이 저장되고 대시보드로 이동한다.
- **재로그인**(이미 프로필이 있는 계정)이면 바로 `/dashboard`로 이동한다.

이 흐름 자체(콜백 파싱 → `oauth-sync` → 온보딩 분기 → 프로필 저장)는 실제 Supabase가
발급한 JWT로 이미 라이브 검증을 마쳤다(2026-07-12) — 카카오/구글 개발자 콘솔 등록만
완료하면 그대로 동작해야 한다. 안 되면 십중팔구 위 1~3번 설정 중 하나가 빠진 것이니
Supabase 대시보드의 Authentication 로그(Logs → Auth Logs)에서 실패 사유를 먼저
확인할 것.
