import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, GatewaysDep, require_self
from app.schemas.consent import ConsentCreate, ConsentRead
from app.schemas.user import UserCreate, UserProfileUpdate, UserRead
from app.services import autobiography_service, character_service, consent_service, user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, gateways: GatewaysDep) -> UserRead:
    """회원가입. 이 엔드포인트만 인증 없이 호출 가능하다(계정이 아직 없으니 당연히
    토큰도 없다) — 나머지 /users/* 및 다른 모든 리소스 라우터는 로그인 토큰이 필요하다.

    계정 생성 자체(이메일 중복 검사 포함)는 Supabase Auth가 담당한다 — 이 프로젝트
    자체 DB(public.users)를 먼저 조회해 중복을 판단하지 않는다(app/services/
    user_service.py 참조), auth.users가 유일한 진실 공급원이기 때문이다."""
    try:
        user = await user_service.create_user(gateways, payload)
    except user_service.EmailAlreadyRegisteredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 등록된 이메일입니다.") from exc
    except user_service.InvalidSignupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return UserRead.model_validate(user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep) -> UserRead:
    require_self(current_user, user_id)
    user = await user_service.get_user(gateways, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "사용자를 찾을 수 없습니다.")
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserProfileUpdate,
    gateways: GatewaysDep,
    current_user: CurrentUserDep,
) -> UserRead:
    """소셜 로그인 온보딩(프로필 완성 단계)이 생년/고향을 채우는 주 용도지만, 일반
    프로필 수정에도 그대로 쓸 수 있게 범용으로 뒀다. 보낸 필드만 갱신된다."""
    require_self(current_user, user_id)
    user = await user_service.update_profile(
        gateways,
        user_id,
        name=payload.name,
        birth_year=payload.birth_year,
        hometown=payload.hometown,
    )
    return UserRead.model_validate(user)


@router.post("/{user_id}/consents", response_model=ConsentRead, status_code=status.HTTP_201_CREATED)
async def create_consent(
    user_id: uuid.UUID, payload: ConsentCreate, gateways: GatewaysDep, current_user: CurrentUserDep
) -> ConsentRead:
    """
    정보주체 동의 기록(기획안 5절). 자녀가 온보딩을 대신 세팅하더라도 데이터 수집·
    이용 동의는 정보주체(부모) 본인에게 직접 받아야 하므로 granted_by로 행위자를
    구분해 남긴다.

    주의: 인증 체계는 계정 = 로그인 세션 하나를 전제로 한다. "자녀가 로그인해 부모를
    대신 동의시키는" granted_by=guardian 흐름은 현재 동일 계정(같은 토큰) 내에서
    이루어지는 것으로 단순화되어 있다 — 자녀 전용 별도 로그인이 필요하다면 이후
    "가족 구성원 초대" 같은 별도 설계가 필요하다(이번 작업 범위 밖).
    """
    require_self(current_user, user_id)
    if payload.character_id is not None:
        # 인물 단위 동의(DISCLOSURE_REALNAME)는 그 인물이 실제로 본인 소유 자서전에
        # 속하는지 검증한다 — 검증 없이는 다른 사용자의 character_id를 넣어 자기
        # 동의 기록에 남의 인물을 엮을 수 있었다(media.py의 session_id 검증과 동일한
        # 교차 테넌트 오염 방지 패턴).
        character = await character_service.get_character(gateways, payload.character_id)
        if character is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "등장인물을 찾을 수 없습니다.")
        autobiography = await autobiography_service.get_autobiography_by_id(
            gateways, character.autobiography_id
        )
        if autobiography.user_id != user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "등장인물을 찾을 수 없습니다.")

    record = await consent_service.record_consent(
        gateways,
        user_id,
        consent_type=payload.consent_type,
        notice_version=payload.notice_version,
        granted_by=payload.granted_by,
        character_id=payload.character_id,
    )
    return ConsentRead.model_validate(record)


@router.get("/{user_id}/consents", response_model=list[ConsentRead])
async def get_consents(
    user_id: uuid.UUID, gateways: GatewaysDep, current_user: CurrentUserDep
) -> list[ConsentRead]:
    require_self(current_user, user_id)
    records = await consent_service.list_consents(gateways, user_id)
    return [ConsentRead.model_validate(record) for record in records]
