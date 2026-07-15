# Azure Computer Vision(물체 탐지·장면 태그) 연동 가이드

코드는 전부 준비되어 있다(`app/clients/azure_vision.py`, `app/services/media_service.py`).
**`AZURE_CV_ENDPOINT`/`AZURE_CV_API_KEY` 두 값만 `.env`에 채우면 코드 수정 없이 바로
동작한다** — Azure 포털에서 리소스를 만들고 그 두 값을 확인하는 절차만 사람이 직접
해야 한다.

## 왜 필요한가

사진(PHOTO) 세션을 열 때 던지는 첫 질문이 사진 내용을 반영하려면(예: "실외에서 사람이
보이는 사진인 것 같은데, 맞나요?") 사진을 미리 분석해 물체·장면과 사진 속 텍스트를
얻어야 한다. 이 프로젝트는 그 분석을 Azure AI Vision의 **Image Analysis 4.0**
API(`features=objects,tags,read` — 물체 탐지 + 장면 태그 + 텍스트 인식) 한 번의
호출로 처리한다. 물체/태그 결과는 영어 키워드로 오므로, Solar로 한국어 명사구 하나로
다듬어 저장한다(`media_service._describe_scene`). 두 값이 비어 있으면
`AzureVisionNotConfiguredError`로 조용히 건너뛰고 일반적인 오프닝 질문으로
대체되므로(앱이 죽지 않음), 설정 전까지는 사진 세션의 질문이 덜 구체적일 뿐이다.

### Caption(자연어 한 문장 요약) 기능을 쓰지 않는 이유

Image Analysis 4.0에는 사진을 한 문장으로 요약해주는 **Caption** 기능도 있어 처음엔
그걸 썼는데, 실제 연동 과정에서 두 가지 제약을 직접 재현·확인하고 포기했다
(2026-07-16):

1. **지역 제약** — Caption은 East US·West US·West Europe·North Europe·
   France Central·Southeast Asia·East Asia·Korea Central 등 일부 지역에서만
   지원된다. 다른 지역으로 리소스를 만들면 리소스 생성 자체는 성공하고 키
   인증도 통과하는데, 실제 분석 호출 시점에만
   `"The feature 'Caption' is not supported in this region."`로 실패한다.
2. **영어 전용 제약** — 심지어 지원 지역이어도 Caption 문장은 **영어로만**
   생성된다(`language=ko` 등 비영어 값을 주면 `NotSupportedLanguage`로 거부됨).

**물체 탐지(objects)와 장면 태그(tags)는 이 두 제약이 전혀 없다** — Caption
지원 목록에 없는 지역(예: Japan East)에서도 정상 동작하는 걸 실제로 확인했다.
다만 결과 라벨 자체가 영어라(예: "person", "outdoor", "grass") 사용자에게 그대로
보여줄 수 없어, Solar로 짧은 한국어 명사구 하나로 다듬는 단계를 코드에 넣었다
(`app/agents/prompts.py:build_scene_description_prompt`). "장소 분석"에 해당하는
정확한 랜드마크 인식 기능은 Image Analysis 4.0에 별도로 없어서, tags가 반환하는
장면/배경 키워드(예: "outdoor", "beach", "building")로 대체한다.

## 1. Azure 포털에서 리소스 찾기/만들기

1. [Azure 포털](https://portal.azure.com)에 로그인한다(팀/조직의 Azure 구독 계정
   필요 — 없으면 무료 체험 계정으로도 이 리소스는 만들 수 있다).
2. 이미 팀이 만들어둔 리소스가 있는지 먼저 확인한다: 포털 상단 검색창에
   **"Computer Vision"** 입력 → 좌측 메뉴 **"모든 리소스"**에서 리소스 종류가
   **Computer Vision**인 항목이 있는지 본다. 있으면 2번 섹션으로 바로 이동.
3. 없으면 새로 만든다: 포털 상단 검색창 → **"Computer Vision"** 검색 → 마켓플레이스
   결과에서 **Computer Vision**(Microsoft 제공) 선택 → **만들기(Create)**.
4. 리소스 생성 폼에서:
   - **구독(Subscription)**: 팀이 쓰는 Azure 구독 선택.
   - **리소스 그룹(Resource group)**: 기존 그룹이 있으면 재사용, 없으면 새로
     만들기(예: `meminisse-rg`).
   - **지역(Region)**: objects/tags/read는 Caption과 달리 지역 제약이 사실상
     없다 — **구독이 배포를 허용하는 지역이면 어디든 괜찮다.**

     ⚠️ 다만 구독 자체가 배포 가능 지역을 제한하는 경우가 있다(신규/체험판·
     Azure for Students 성격의 구독에서 흔함). 이 경우 지역과 무관하게 아래
     에러가 뜬다:
     ```
     InvalidTemplateDeployment / RequestDisallowedByAzure
     "This policy maintains a set of best available regions where your
     subscription can deploy resources..."
     ```
     **"Azure for Students" 구독에서 실제로 겪은 사례(2026-07-16):** East US,
     West Europe, West US 2, North Europe 네 지역은 이 정책으로 막혔지만
     **Japan East는 통과**했다 — 서구권 플래그십 지역이 막혀도 아시아·태평양
     지역은 열려 있을 가능성이 있으니, 막히면 지역을 몇 개 바꿔가며 시도해볼
     것(Caption을 안 쓰므로 어느 지역이든 objects/tags/read 자체는 동작한다 —
     구독 정책 통과 여부만 관건).
   - **이름(Name)**: 전역적으로 고유해야 한다(예: `meminisse-vision`).
   - **가격 책정 계층(Pricing tier)**: 개발/해커톤 단계라면 **F0(무료, 분당 20회·
     월 5,000회 제한)**로 충분하다. 실사용 트래픽이 늘면 **S1**로 전환.
5. **검토 + 만들기 → 만들기** 클릭 후 배포 완료를 기다린다(보통 1분 이내).

## 2. Endpoint / Key 값 확인 위치

리소스 생성이 끝나면 **"리소스로 이동"**을 누르거나, 포털에서 방금 만든 리소스를
직접 연다. 리소스 관리 화면 좌측 메뉴에서:

- **개요(Overview)** 탭: 상단 요약에 **"엔드포인트(Endpoint)"**가 바로 보인다.
  `https://<리소스이름>.cognitiveservices.azure.com` 형태 — 이 값이
  `AZURE_CV_ENDPOINT`다.
- 좌측 메뉴 **"키 및 엔드포인트(Keys and Endpoint)"**: **KEY 1** 또는 **KEY 2** 중
  아무거나 하나(👁 아이콘/복사 아이콘으로 값 확인·복사) — 이 값이
  `AZURE_CV_API_KEY`다. 같은 화면에 엔드포인트도 다시 한번 표시된다(둘 다 여기서
  한 번에 복사 가능).

키 2개(KEY 1/KEY 2)가 있는 이유는 무중단 키 교체용이다 — 하나를 쓰다가 노출 등의
이유로 교체가 필요하면, 안 쓰던 나머지 키로 `.env`를 먼저 바꾸고 배포한 뒤
포털에서 기존 키를 재생성(regenerate)하면 서비스 중단 없이 교체된다.

## 3. `.env`에 값 채우기

`backend/.env`(신규 환경이면 `.env.example`을 복사해서 시작):

```
AZURE_CV_ENDPOINT=https://<리소스이름>.cognitiveservices.azure.com
AZURE_CV_API_KEY=<KEY 1 또는 KEY 2>
```

이 값들은 `app/config.py`의 `Settings.AZURE_CV_ENDPOINT`/`AZURE_CV_API_KEY`로
읽힌다(둘 다 문자열, 기본값은 빈 문자열 — 비어 있으면 위에서 설명한 대로 조용히
건너뜀). `.env`는 `.gitignore`에 이미 포함돼 있어 커밋되지 않는다 — 절대 이 키를
코드나 커밋 메시지에 직접 적지 말 것.

## 4. 동작 확인 방법

전체 업로드 UI를 거치지 않고, `.env`를 채운 직후 `app/clients/azure_vision.py`를
직접 호출해 키/엔드포인트가 맞는지 먼저 확인하는 편이 빠르다(실제 사진 없이
Pillow로 임시 이미지를 만들어 호출):

```python
import asyncio, io
from PIL import Image
from app.clients import azure_vision

async def main():
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), color=(135, 206, 235)).save(buf, format="PNG")
    result = await azure_vision.analyze_image(buf.getvalue())
    print("objects:", azure_vision.extract_objects(result))
    print("tags:", azure_vision.extract_tags(result))
    print("read_text:", azure_vision.extract_read_text(result))

asyncio.run(main())
```

- 성공하면(단순 도형 이미지라도) `tags`에 뭔가 하나 이상 찍힌다(실제로 확인:
  `["colorfulness", "graphics"]` 등). 실제 사진이 아니라 물체가 없는 단순
  이미지라 `objects`는 보통 빈 리스트다.
- `401`이면 키 오타/미갱신, `404`면 엔드포인트 URL 오타.
- `objects,tags,read`는 특정 기능 때문에 지역이 막히는 일이 없으므로, 이 조합
  자체가 4xx로 실패한다면 지역보다는 키/엔드포인트/구독 상태를 먼저 의심할 것.

한국어 오프닝 질문까지 이어지는 전체 파이프라인(Azure Vision → Solar 요약)을
확인하려면:

```python
from app.services.media_service import _describe_scene
# 위 result에서 얻은 objects/tags를 그대로 사용
description = await _describe_scene(objects=objects, tags=tags)
print(description)  # 예: "그래픽과 색감이 풍부한 사진"
```

이 스크립트로 문제없음을 확인한 뒤에 실제 업로드 플로우로 넘어간다:

1. 백엔드를 재시작한다(`.env` 값은 프로세스 시작 시 한 번만 읽는다 —
   `fastapi dev`/Celery 워커 둘 다 재시작 필요, `docker-compose.yml`의 worker
   서비스를 쓴다면 `docker compose restart worker`).
2. 프론트에서 사진을 업로드해 PHOTO 세션을 하나 만들어본다.
3. 오프닝 질문이 일반적인 문구("이 사진에 대해 더 자세히 이야기를 들려주시겠어요?")
   대신 사진 내용을 반영한 구체적인 문구로 뜨면 정상 연동된 것이다.
4. `MediaAsset.pre_extracted_labels`에 Azure의 원시 응답이 그대로 저장되므로
   (`app/services/media_service.py`), 실제 사진에서 `objectsResult.values[]`가
   기대한 형태로 오는지 이 컬럼으로 확인할 수 있다.

**실사진으로 전체 파이프라인 검증 완료(2026-07-16):** 강·산·나무가 있는 실제
풍경 사진으로 테스트한 결과, `objectsResult.values[0]`이
`{"boundingBox": {...}, "tags": [{"name": "Maple", "confidence": 0.555}]}`
형태로 와서 `extract_objects`의 파싱 가정이 정확했음을 확인했다. tags도
`outdoor(0.99)`, `sky(0.98)`, `water(0.97)`, `mountain(0.66)`, `tree(0.60)` 등
사진 내용과 실제로 일치하는 라벨을 신뢰도 순으로 정확히 반환했다.

한 가지 버그를 이 실사진 테스트에서 발견해 수정했다: `_describe_scene`이
`reasoning_effort="low"`일 때 입력(영어 태그)을 그대로 따라 **영어로**
답해버리는 경우가 있었다(4회 중 1회, 예: `"maple tree in outdoor landscape"`).
프롬프트에 "출력은 반드시 한국어" 규칙과 반례를 명시하고 `reasoning_effort`를
`"medium"`으로 올리자 6회 중 6회 한국어로 안정화됐다(다만 6회 중 1회는 문장은
한국어이되 물체 이름 "Maple" 자체가 번역 안 된 채 섞여 나온 경우가 있었다 —
"maple 나무와 강, 산이 보이는 풍경"처럼 완전히 자연스럽진 않지만 실사용에
지장은 없는 수준).

**두 번째 실사진(아이 두 명이 있는 흑백 사진)으로 검증 중 발견한 버그 하나 더:**
`extract_objects`가 같은 이름("person")을 중복 제거해버려서, 실제로는 사람이
2명 감지됐는데도 최종 한국어 설명이 "아이"(단수)로 뭉개졌다(장면 태그에
"boy"·"girl"이 각각 잡혔는데도 활용되지 못함). `extract_objects`는 이제 중복을
제거하지 않고 그대로 반환하고, `build_scene_description_prompt`가 "person×2"
처럼 개수를 표기해 LLM에 명시적으로 알려준다 — 프롬프트에도 개수 표기(×N)를
해석해 반영하고, 더 구체적인 태그(boy/girl 등)를 일반 이름(person)보다
우선하라는 지시를 추가했다. 재검증 결과 5회 중 5회 "소년과 소녀"처럼 정확한
인원수·구체성이 반영됐다.

**세 번째 실사진(부모 2명+자녀 3명, 총 5명이 있는 세피아톤 가족사진)으로 검증
중 발견한, 코드로는 고칠 수 없는 한계(2026-07-16):**
- 물체 탐지가 5명 중 4명만 찾았다(1명 누락) — Azure 모델 자체의 탐지 정확도
  한계.
- 더 중요한 건 tagsResult 원본 응답 자체에 "man"/"woman"/"adult" 같은 성인
  관련 라벨이 **단 하나도 없었다**("toddler"/"boy"/"child"만 있었음) — 이건
  `extract_tags`가 신뢰도로 걸러낸 게 아니라 Azure가 애초에 반환하지 않은
  것이라, 프롬프트나 파싱 로직으로 고칠 수 있는 문제가 아니다. 그 결과 최종
  한국어 설명이 부모를 언급하지 않고 "아이들"로만 묘사됐다(5회 모두 재현).

이 결과로 판단하기로: 이런 케이스는 **Azure Vision 모델의 인식 한계로 받아들이고
추가 프롬프트 보정은 하지 않는다.** 오프닝 질문은 애초에 "~인 것 같은데, 맞나요?"
가 아니라 자유 서술을 유도하는 형태라(build_photo_session_opening 참조), 설명이
다소 부정확해도 실제 대화에서 사용자가 바로잡을 여지가 있다는 게 이 판단의
근거다.

**네 번째 실사진(장난감 자동차 여러 대를 클로즈업한 사진)으로 검증 중 발견한,
같은 종류의 한계(2026-07-16):**
- objectsResult가 "car"/"Land vehicle"로, tagsResult 원본에는 "text"/"indoor"
  단 2개만 반환됐다 — **"toy"/"miniature"/"model" 라벨은 원본 응답 어디에도
  없었다.** 즉 Azure가 장난감이 아니라 실제 자동차로 인식했다는 뜻이다. 크기를
  가늠할 기준(손·사람·익숙한 배경 등)이 없는 클로즈업 사진에서는 장난감과
  실물을 구분하기 어려운 비전 모델의 예상 가능한 한계다.
- readResult는 "AMBULANCE"/"TAXI"/"POLICE"를 정확히 읽어냈다(OCR 자체는 정상
  동작). 다만 이 OCR 텍스트를 `_describe_scene`(장면 설명 합성)에 함께
  넘겨준다고 해도 "장난감"이라는 인식이 좋아지지는 않을 것으로 판단해 시도하지
  않았다 — 근본 원인이 이미 끝난 물체 탐지 단계에 있어 그 뒤 단계(Solar 문구
  다듬기)에서 되돌릴 수 없고, 설령 OCR 텍스트로부터 "장난감일 것"이라고
  추론하게 하려면 "목록에 없는 내용을 지어내지 말라"는 이 프롬프트의 핵심
  제약과 충돌한다. 그 제약을 풀면 이번엔 맞아도 실제 구급차·택시·경찰차가
  나란히 찍힌 사진에서는 같은 논리로 "장난감"이라고 잘못 추론할 위험이 커진다
  (이 프로젝트 전반의 "애매하면 지어내지 않는다" 원칙과도 배치됨).

세 번째·네 번째 사례 모두 같은 결론: **Azure Vision이 애초에 탐지하지 못한
속성(성인 여부, 장난감 여부)은 이 프로젝트의 Solar 후처리 단계로 보정하려 하지
않는다.**
