"""고정 인터뷰 질문 100개의 단일 원본 데이터.

alembic/versions/006_seed_questions.py(실 DB 시드)와 app/gateways/mock/store.py
(로컬/테스트용 인메모리 시드)가 이 목록을 함께 참조한다 — 같은 질문 내용을 두 곳에
따로 옮겨 적다 내용이 어긋나는 사고를 막기 위함이다.

sequence_order는 유년기→청년기→장년기→노년기 순서의 전역 연속 번호다(questions
테이블 전체에 유니크 제약, alembic/versions/001_initial_schema.py 참조). 이
서비스는 생애주기를 넘나들며 질문을 자유롭게 고르지 않고, 이 순서 그대로 한 사람의
인터뷰 질문 큐로 삼는다 — 나이 들어가는 순서를 그대로 따라간다.

생애주기별로 정확히 25문항씩(총 100문항) 배정되어 있다(질문 리스트.md 원본,
2026-07-15 확정). 각 질문의 "suggested_tags"는 DB 컬럼도 아니고 QuestionGateway/
MockStore 어느 쪽도 읽지 않는 순수 문서용 필드다 — 원본 질문 리스트가 각 질문마다
붙여둔 "이 질문 답변이 향후 자서전 커스터마이징(말투/구성/컨셉, app/agents/
prompts.py의 TONE_OPTIONS/STRUCTURE_OPTIONS/CONCEPT_OPTIONS)에서 어떤 선택지와
어울릴 법한지"에 대한 자유 서술 힌트를 그대로 보존해 둔 것이다. 원본 표기가
TONE_OPTIONS 등의 정식 키/name과 항상 정확히 일치하지는 않아(예: 축약형 "공간
중심" vs 정식 name "공간 및 장소 중심 구성", 혹은 세 항목이 말투/구성/컨셉 각
카테고리에 고르게 대응하지 않는 경우도 있음), 정규화 매핑(원문 태그 → 정식 옵션
키)은 app/agents/prompts.py의 _TAG_TO_OPTION이 사람이 검토해 만들어 따로 갖고
있다 — 이 파일은 원본 표기를 그대로만 보존한다(app/services/autobiography_
service.py:get_customization_recommendations가 두 파일을 이어 실제 추천을 만든다).

"eligibility"(선택 필드, 대다수 질문엔 없음)는 이 질문이 특정 프로필을 전제로 할 때만
붙인다 — 예: 자녀가 없으면 답할 수 없는 질문. 가입 온보딩에서 라디오 버튼으로 직접
입력받는 User.education_level/marital_status/has_children(2026-07-16 설계, 대화
추론이 아닌 명시적 입력)을 기준으로 interview_service.py가 평가한다. 형태는
{"field": <User의 컬럼명>, "requires": <bool>} 또는
{"field": <컬럼명>, "requires_one_of": [<enum 값들>]} 둘 중 하나다. 그 필드가
None(응답 안 함=모름)이면 항상 통과시킨다 — 모르면 안전하게 묻는 쪽이 기본값이고,
명확히 어긋난다고 확인된 경우에만 건너뛴다."""

QUESTION_BANK: list[dict] = [
    # ---------------------------------------------------------------- #
    # 제1장. 유년기 및 청소년기 (0~20세) — sequence_order 1~25            #
    # ---------------------------------------------------------------- #
    {
        "sequence_order": 1,
        "life_period": "childhood",
        "title": "첫 기억/공간",
        "content": "내 인생의 가장 오래된 첫 기억, 혹은 문을 열고 나가면 보이던 옛 동네의 풍경과 집 안에서 가장 좋아했던 나만의 공간(다락방, 마루, 장독대 등)에 얽힌 이야기를 들려주세요.",
        "suggested_tags": ["공간 및 장소 중심", "특정 시기 집중 조명", "소설적 서술체"],
    },
    {
        "sequence_order": 2,
        "life_period": "childhood",
        "title": "감각적 기억",
        "content": "어릴 적 살던 동네의 골목길이나 시장, 혹은 학교 가는 길에서 맡았던 냄새나 자주 들리던 소리 중 아직도 뚜렷하게 기억나는 것이 있나요?",
        "suggested_tags": ["공간 중심", "생애 전반 회고록", "소설적 서술체"],
    },
    {
        "sequence_order": 3,
        "life_period": "childhood",
        "title": "명절과 계절",
        "content": "유년 시절 매미 허물을 줍던 여름날, 꽁꽁 언 논에서 썰매를 타던 겨울날, 혹은 전 부치는 냄새가 진동하던 명절날 중 가장 선명한 하루를 묘사해 주세요.",
        "suggested_tags": ["특정 시기 집중", "관조적 에세이", "소설적 서술체"],
    },
    {
        "sequence_order": 4,
        "life_period": "childhood",
        "title": "특별한 음식",
        "content": "특별한 날(소풍, 아버지 월급날 등)에만 먹을 수 있었던 가장 기억에 남는 음식과, 그 음식을 먹을 때 둘러앉아 있던 식구들의 표정을 기억하시나요?",
        "suggested_tags": ["결정적 에피소드", "가족사", "따뜻한 대화체"],
    },
    {
        "sequence_order": 5,
        "life_period": "childhood",
        "title": "소중한 보물",
        "content": "어릴 적 누구에게도 뺏기고 싶지 않았던 나만의 보물 1호(장난감, 책, 혹은 낡은 담요나 주머니 속 구슬 등)는 무엇이었고, 왜 그렇게 아끼셨나요?",
        "suggested_tags": ["테마-사물", "덕업일치", "3인칭 관찰자 평전"],
    },
    {
        "sequence_order": 6,
        "life_period": "childhood",
        "title": "부모님 1",
        "content": "부모님을 떠올리면 가장 먼저 생각나는 강렬한 장면은 무엇인가요? 큰 가르침을 받았던 일이나, 반대로 마음에 상처나 결핍으로 남은 일화가 있다면 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "가족사", "내밀한 고백체"],
    },
    {
        "sequence_order": 7,
        "life_period": "childhood",
        "title": "부모님 2",
        "content": "어릴 적 크게 아팠을 때, 밤새 머리맡을 지켜주시던 부모님(혹은 조부모님)의 손길이나 당시 캄캄했던 방 안의 분위기를 기억하시나요?",
        "suggested_tags": ["결정적 에피소드", "가족사", "친근한 대화체"],
    },
    {
        "sequence_order": 8,
        "life_period": "childhood",
        "title": "부모님 3",
        "content": "'우리 부모님도 결국 완벽하지 않은 평범한 어른이구나'라고 처음으로 깨닫게 된 구체적인 사건이나, 부모님의 뒷모습을 보고 묘한 감정을 느꼈던 일화가 있었나요?",
        "suggested_tags": ["테마-가족", "철학 사전", "관조적 에세이체"],
    },
    {
        "sequence_order": 9,
        "life_period": "childhood",
        "title": "형제자매 1",
        "content": "형제자매들과 함께 자라면서 겪었던 잊지 못할 사건을 들려주세요. 끈끈하게 의지했던 일도 좋고, 크게 다투거나 차별 대우를 받아 억울했던 기억도 좋습니다.",
        "suggested_tags": ["연대기 구성", "가족사", "친근한 대화체"],
    },
    {
        "sequence_order": 10,
        "life_period": "childhood",
        "title": "형제자매 2",
        "content": "당시 집안 형제들 사이에서 든든한 맏이, 심부름꾼, 혹은 사고뭉치 막내 등 어떤 역할을 맡으셨나요? 당시 본인의 포지션을 보여주는 에피소드를 들려주세요.",
        "suggested_tags": ["테마별 구성", "3인칭 평전", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 11,
        "life_period": "childhood",
        "title": "어릴 적 두려움",
        "content": "어릴 적 유난히 무서워했던 장소나 존재(예: 재래식 화장실, 동네의 사나운 개, 전설 등)가 있었나요? 무서워서 벌벌 떨었던 귀여운 일화 등을 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "생애 전반 회고록", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 12,
        "life_period": "childhood",
        "title": "학교 입학",
        "content": "코흘리개 시절, 처음 학교에 입학하던 날 메고 갔던 가방의 색깔이나 낯선 교실에 들어섰을 때의 긴장했던 아침 공기를 기억하시나요?",
        "suggested_tags": ["역순행적 구성", "생애 전반 회고록", "담담한 평어체"],
    },
    {
        "sequence_order": 13,
        "life_period": "childhood",
        "title": "동네 놀이",
        "content": "동네 골목이나 공터에서 친구들과 해가 질 때까지 하던 놀이(딱지치기, 고무줄놀이 등) 중 가장 자신 있었던 것은 무엇이었고, 누구와 주로 어울렸나요?",
        "suggested_tags": ["공간 중심", "취미 몰입기", "친근한 대화체"],
    },
    {
        "sequence_order": 14,
        "life_period": "childhood",
        "title": "군것질과 하굣길",
        "content": "초등학교(국민학교) 하굣길에 친구들과 사 먹었던 가장 기억에 남는 불량식품이나 군것질거리, 그리고 그때의 왁자지껄한 골목길 풍경을 들려주세요.",
        "suggested_tags": ["공간 중심", "생애 전반 회고록", "소설적 서술체"],
    },
    {
        "sequence_order": 15,
        "life_period": "childhood",
        "title": "첫 소풍/운동회",
        "content": "김밥을 싸 들고 갔던 첫 소풍이나 운동회 날, 만국기가 펄럭이던 학교 운동장의 풍경과 달리기 출발선에 섰을 때의 설렘을 묘사해 주시겠어요?",
        "suggested_tags": ["특정 시기 집중", "생애 전반 회고록", "소설적 서술체"],
    },
    {
        "sequence_order": 16,
        "life_period": "childhood",
        "title": "큰 꾸중",
        "content": "어린 시절 부모님이나 어른들에게 가장 크게 혼났던 잊지 못할 사건은 무엇인가요? 그때 어떤 잘못을 했고, 회초리를 맞거나 벌을 서면서 어떤 기분이었는지 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "가족사", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 17,
        "life_period": "childhood",
        "title": "나만의 일탈",
        "content": "부모님께 끝까지 들키지 않았던 비밀, 혹은 친구들과 작당 모의를 하고 저질렀던 일탈이나 장난친 경험이 있다면 구체적으로 들려주세요.",
        "suggested_tags": ["테마별 구성", "유머러스한 풍자체", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 18,
        "life_period": "childhood",
        "title": "은사님",
        "content": "학창 시절, 내 인생에 깊은 인상을 남긴 선생님에 대한 일화를 들려주세요. 나를 따뜻하게 이끌어주신 분도 좋고, 부당하게 상처를 주어 반면교사로 삼게 된 분도 좋습니다.",
        "suggested_tags": ["테마-인연", "멘토링 대담집", "대중 강연체"],
    },
    {
        "sequence_order": 19,
        "life_period": "childhood",
        "title": "별명",
        "content": "학창 시절에 불리던 별명이 있으셨나요? 그 별명은 어떤 외모적 특징이나 구체적인 사건 때문에 생기게 된 건가요?",
        "suggested_tags": ["연대기 구성", "3인칭 관찰자 평전", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 20,
        "life_period": "childhood",
        "title": "몰입과 취미",
        "content": "10대 시절, 방 벽에 붙어 있던 포스터나 돈을 모아 열광적으로 사 모았던 물건, 혹은 밤새워 듣던 라디오 프로그램이 있었나요?",
        "suggested_tags": ["특정 시기 집중", "취미 몰입기", "소설적 서술체"],
    },
    {
        "sequence_order": 21,
        "life_period": "childhood",
        "title": "결핍/가난",
        "content": "어린 시절 겪었던 가난이나 결핍에 대한 뚜렷한 장면은 무엇인가요? (예: 남들과 달랐던 도시락 반찬, 낡은 운동화, 수학여행 등) 그날 하루를 들려주세요.",
        "suggested_tags": ["역순행적 구성", "실패와 재기", "담담한 평어체"],
    },
    {
        "sequence_order": 22,
        "life_period": "childhood",
        "title": "꿈과 다짐",
        "content": "어릴 적, 마음속에 품고 있던 구체적인 장래 희망이나 밤에 책상머리에서 남몰래 했던 굳은 다짐(현실을 벗어나기 위한 독기 등)이 있다면 무엇이었나요?",
        "suggested_tags": ["테마-꿈", "비즈니스 & 리더십", "편지체"],
    },
    {
        "sequence_order": 23,
        "life_period": "childhood",
        "title": "사춘기의 반항",
        "content": "중고등학교 시절, 부모님이나 세상에 대해 알 수 없는 반항심이 끓어올라 충동적으로 저질렀던 가출이나 반항의 하루가 있었다면 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "내밀한 고백체", "3인칭 평전"],
    },
    {
        "sequence_order": 24,
        "life_period": "childhood",
        "title": "졸업식",
        "content": "중학교나 고등학교 졸업식 날, 밀가루를 뒤집어쓰거나 짜장면을 먹었던 기억 등 10대를 마무리하던 날의 구체적인 풍경과 에피소드가 있나요?",
        "suggested_tags": ["연대기 구성", "생애 전반 회고록", "객관적 기록체"],
    },
    {
        "sequence_order": 25,
        "life_period": "childhood",
        "title": "자유 발언",
        "content": "그 외에 유년기나 청소년기 시절을 생각하면 툭 튀어나오는 특정한 장소나 물건, 남기고 싶은 일화가 있다면 자유롭게 말씀해 주세요.",
        "suggested_tags": ["자유 구성", "생애 전반 회고록", "가상의 인터뷰체"],
    },
    # ---------------------------------------------------------------- #
    # 제2장. 청년기 (20~35세) — sequence_order 26~50                     #
    # ---------------------------------------------------------------- #
    {
        "sequence_order": 26,
        "life_period": "youth",
        "title": "첫 출발",
        "content": "스무 살 무렵, 성인으로서 마주했던 첫 번째 낯선 세상(대학 입학, 첫 취업, 타지 상경, 군대 등)에 첫발을 내디뎠던 날의 구체적인 상황과 그때의 심정을 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "특정 시기 집중", "담담한 평어체"],
    },
    {
        "sequence_order": 27,
        "life_period": "youth",
        "title": "첫 자취방",
        "content": "처음 부모님의 품을 떠나 머물렀던 '나만의 첫 방(단칸방, 하숙집 등)'은 어떤 모습이었나요? 방의 크기나 창문 밖 풍경, 겨울의 웃풍 등을 묘사해 주시겠어요?",
        "suggested_tags": ["공간 중심", "생애 전반 회고록", "소설적 서술체"],
    },
    {
        "sequence_order": 28,
        "life_period": "youth",
        "title": "타향살이의 서러움",
        "content": "타지 상경이나 유학 등 낯선 곳에서 홀로 지내며, 아픈데 약도 없고 서러움이 북받쳐 올라 이불을 뒤집어쓰고 울었던 구체적인 밤의 기억을 들려주세요.",
        "suggested_tags": ["공간 중심", "실패와 재기의 기록", "내밀한 고백체"],
    },
    {
        "sequence_order": 29,
        "life_period": "youth",
        "title": "몰입의 장소",
        "content": "20대 시절, 잠자는 곳을 제외하고 하루 중 가장 많은 시간을 보냈던 장소(예: 공장, 도서관, 다방, 시장 골목 등)의 풍경과, 그곳에서 주로 무엇을 하셨는지 들려주세요.",
        "suggested_tags": ["공간 중심", "특정 시기 집중", "객관적 기록체"],
    },
    {
        "sequence_order": 30,
        "life_period": "youth",
        "title": "출퇴근길의 기억",
        "content": "청년 시절, 이른 새벽 출근길이나 늦은 밤 막차를 타고 퇴근하던 길에 창밖을 보며 느꼈던 고단함과 스스로에게 했던 다짐을 구체적으로 들려주세요.",
        "suggested_tags": ["공간 중심", "특정 시기 집중", "내밀한 고백체"],
    },
    {
        "sequence_order": 31,
        "life_period": "youth",
        "title": "생애 첫 여행",
        "content": "스무 살 무렵, 친구들과 난생처음 기차를 타고 떠났던 바다나 엠티(MT) 등 가슴 뛰었던 첫 여행의 풍경과 당시 겪었던 해프닝을 기억하시나요?",
        "suggested_tags": ["결정적 에피소드", "청춘의 낭만", "친근한 대화체"],
    },
    {
        "sequence_order": 32,
        "life_period": "youth",
        "title": "시대적 사건",
        "content": "88올림픽, 2002 월드컵, 민주화 운동 등 나라 전체가 들썩였던 역사적 사건이 있던 날, 선생님은 구체적으로 어떤 장소에서 누구와 무엇을 하고 계셨나요?",
        "suggested_tags": ["연대기 구성", "객관적 기록체", "3인칭 평전"],
    },
    {
        "sequence_order": 33,
        "life_period": "youth",
        "title": "청춘의 유행",
        "content": "20대 시절, 큰맘 먹고 샀던 유행하던 옷(나팔바지, 미니스커트 등)이나 한껏 멋을 부리고 외출했던 날 거울 속에 비친 내 모습을 기억하시나요?",
        "suggested_tags": ["특정 시기 집중", "관조적 에세이", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 34,
        "life_period": "youth",
        "title": "청춘의 문화",
        "content": "20대 시절 가장 많이 들었던 노래 테이프(LP, CD)나, 친구들과 술잔을 기울이며 목청 높여 열창했던 십팔번 곡은 무엇이었고, 그 노래에 얽힌 사연이 있나요?",
        "suggested_tags": ["덕업일치", "관조적 에세이체", "소설적 서술체"],
    },
    {
        "sequence_order": 35,
        "life_period": "youth",
        "title": "첫 술자리",
        "content": "어른이 되어 처음으로 쓴 소주(혹은 맥주)를 마셨던 날의 분위기, 혹은 그 시절 친구들과 부어라 마셔라 한 뒤 다음 날 아침 겪었던 쓰라린 숙취의 에피소드를 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "친근한 대화체", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 36,
        "life_period": "youth",
        "title": "기다림의 편지",
        "content": "휴대폰이 없던 시절, 밤새 꾹꾹 눌러 쓴 연애편지나 위문편지처럼, 우체통을 서성이며 누군가의 소식을 애타게 기다렸던 애틋한 기억이 있나요?",
        "suggested_tags": ["사물 중심", "소설적 서술체", "생애 전반 회고록"],
    },
    {
        "sequence_order": 37,
        "life_period": "youth",
        "title": "홀로서기의 고단함",
        "content": "부모님 품을 떠나 내 힘으로 홀로서기를 했을 때, 밥벌이의 고단함을 뼈저리게 느꼈던 구체적인 하루(예: 상사의 꾸중, 굶주림 등)를 들려주세요.",
        "suggested_tags": ["테마-독립", "비즈니스 & 리더십", "내밀한 고백체"],
    },
    {
        "sequence_order": 38,
        "life_period": "youth",
        "title": "떨리던 시험날",
        "content": "입학시험, 취업 면접, 혹은 운전면허 시험처럼 20대 시절 내 심장을 가장 크게 뛰게 했던 '결전의 날' 아침의 풍경과 수험표를 쥐었던 손의 촉감을 묘사해 주세요.",
        "suggested_tags": ["특정 시기 집중", "비즈니스 & 리더십", "담담한 평어체"],
    },
    {
        "sequence_order": 39,
        "life_period": "youth",
        "title": "짠내 나는 시절",
        "content": "데이트 비용이 모자라 쩔쩔맸거나, 돈을 아끼려 며칠 내내 라면만 먹었지만 그래도 낭만과 웃음이 있었던 20대 시절의 짠내 나는 에피소드를 들려주세요.",
        "suggested_tags": ["실패와 재기의 기록", "유머러스한 풍자체", "친근한 대화체"],
    },
    {
        "sequence_order": 40,
        "life_period": "youth",
        "title": "첫 월급",
        "content": "내 손으로 땀 흘려 벌었던 '첫 돈(월급, 수확 등)'을 손에 쥐었을 때의 기분과, 그 돈으로 부모님께 사드렸던 빨간 내복 같은 물건에 얽힌 일화를 들려주세요.",
        "suggested_tags": ["연대기 구성", "비즈니스 & 리더십", "객관적 기록체"],
    },
    {
        "sequence_order": 41,
        "life_period": "youth",
        "title": "내 돈으로 산 물건",
        "content": "월급을 모아 내 돈으로 처음 장만했던 비싼 물건(첫 정장, 오디오, 자전거 등)은 무엇이었고, 그것을 처음 내 방에 두었을 때 어떤 기분이었나요?",
        "suggested_tags": ["테마-사물", "비즈니스 & 리더십", "담담한 평어체"],
    },
    {
        "sequence_order": 42,
        "life_period": "youth",
        "title": "첫사랑/연애",
        "content": "누군가를 열렬히 좋아했던 짝사랑, 가슴 아팠던 연애, 혹은 잊지 못할 인연을 처음 만나게 된 순간 등 '사랑'이라는 감정과 얽힌 구체적인 에피소드를 하나 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "특정 시기 집중", "소설적 서술체"],
    },
    {
        "sequence_order": 43,
        "life_period": "youth",
        "title": "무모한 도전",
        "content": '"내가 그때 왜 그랬을까?" 싶을 정도로 앞뒤 재지 않고 무모하게 도전해 본 일이나, 젊은 혈기에 저질렀던 아찔한 사건에 대한 이야기를 들려주세요.',
        "suggested_tags": ["결정적 에피소드", "실패와 재기의 기록", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 44,
        "life_period": "youth",
        "title": "동료와 인연",
        "content": "매일같이 붙어 다녔던 단짝 친구나 힘든 일터에서 의지했던 동료와 겪은 잊지 못할 일화를 들려주세요. 크게 다투거나 멀어지게 된 일도 좋습니다.",
        "suggested_tags": ["테마-관계", "멘토링 대담집", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 45,
        "life_period": "youth",
        "title": "첫 실패",
        "content": "성인이 되어 처음으로 세상이 내 맘대로 되지 않는다는 걸 느꼈던 '첫 좌절'이나 '실패(낙방, 부도 등)'의 차가운 현실 벽에 부딪혔을 때의 상황을 들려주세요.",
        "suggested_tags": ["역순행적 구성", "실패와 재기의 기록", "편지체"],
    },
    {
        "sequence_order": 46,
        "life_period": "youth",
        "title": "치명적 실수",
        "content": "청년기 시절, '내 인생 최대의 헛발질'이라고 부를 만큼 큰돈을 날렸거나 이불을 걷어찰 만큼 황당하고 부끄러운 실수담이 있다면 하나 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "실패와 재기", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 47,
        "life_period": "youth",
        "title": "위로의 공간",
        "content": "지치고 힘들 때마다 찾아가 위로를 받았던 단골 식당, 술집, 혹은 포장마차가 있었나요? 그곳에서 주로 누구와 어떤 안주를 놓고 한탄(다짐)을 하셨나요?",
        "suggested_tags": ["공간 중심", "관조적 에세이체", "친근한 대화체"],
    },
    {
        "sequence_order": 48,
        "life_period": "youth",
        "title": "장래희망과 현실",
        "content": "20대 시절, 가장 큰 꿈을 이루기 위해 구체적으로 어떤 노력들을 하셨나요? 혹은 현실적 문제 때문에 눈물을 머금고 잠시 접어둬야 했던 날의 기억이 있나요?",
        "suggested_tags": ["테마-성장", "멘토링 대담집", "대중 강연체"],
    },
    {
        "sequence_order": 49,
        "life_period": "youth",
        "title": "30대를 앞두고",
        "content": "서른 살을 앞두고 20대의 마지막 끝자락에 섰을 때, 지난 10년을 돌아보며 내 마음속에 어떤 독기나 가치관이 굳게 자리 잡게 되었는지 사건과 함께 들려주세요.",
        "suggested_tags": ["테마-철학", "가치관 및 철학 사전", "대중 강연체"],
    },
    {
        "sequence_order": 50,
        "life_period": "youth",
        "title": "자유 발언",
        "content": "그 외에 뜨거웠던 청년기의 일화나 꼭 기록으로 남기고 싶은 사건, 물건, 인물이 있다면 자유롭게 말씀해 주세요.",
        "suggested_tags": ["연대기 구성", "생애 전반 회고록", "가상의 인터뷰체"],
    },
    # ---------------------------------------------------------------- #
    # 제3장. 장년기 (35세~은퇴 시점) — sequence_order 51~75               #
    # ---------------------------------------------------------------- #
    {
        "sequence_order": 51,
        "life_period": "adulthood",
        "title": "배우자와의 만남",
        "content": "평생의 인연을 처음 만나 설레었던 풍경부터, 연애 시절을 거쳐 '아, 이 사람과 평생을 함께해야겠다'고 마음먹게 된 계기와 결혼식 날의 일화를 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "가족사 및 양육기", "소설적 서술체"],
        "eligibility": {"field": "marital_status", "requires_one_of": ["married", "divorced", "widowed"]},
    },
    {
        "sequence_order": 52,
        "life_period": "adulthood",
        "title": "부모가 되던 날",
        "content": "가정을 꾸리고 첫아이를 품에 안았을 때, 연애 시절엔 몰랐던 현실의 무게감과 '부모'라는 이름표가 주는 막중한 책임감을 온몸으로 느꼈던 그날을 들려주세요.",
        "suggested_tags": ["연대기 구성", "가족사 및 양육기", "내밀한 고백체"],
        "eligibility": {"field": "has_children", "requires": True},
    },
    {
        "sequence_order": 53,
        "life_period": "adulthood",
        "title": "아이들과의 여행",
        "content": "빠듯한 살림에도 아이들을 위해 큰맘 먹고 떠났던 첫 가족여행, 혹은 잊지 못할 여름휴가 때 계곡이나 바다에서 왁자지껄했던 구체적인 풍경을 들려주세요.",
        "suggested_tags": ["공간 중심", "가족사 및 양육기", "따뜻한 대화체"],
        "eligibility": {"field": "has_children", "requires": True},
    },
    {
        "sequence_order": 54,
        "life_period": "adulthood",
        "title": "자녀의 첫 성취",
        "content": "아이가 처음으로 상장을 받아오거나 대학에 합격했던 날처럼, 내 일보다 더 기뻐서 동네방네 자랑하고 싶었던 가슴 벅찬 하루를 들려주세요.",
        "suggested_tags": ["테마-가족", "가족사 및 양육기", "친근한 대화체"],
        "eligibility": {"field": "has_children", "requires": True},
    },
    {
        "sequence_order": 55,
        "life_period": "adulthood",
        "title": "나의 집 마련",
        "content": "셋방살이를 전전하다 번듯한 내 집(전세 포함)을 마련하고 처음으로 우리 집 문을 열고 들어가던 날, 텅 빈 거실에서 느꼈던 첫 공기와 벅찬 기분을 기억하시나요?",
        "suggested_tags": ["공간 중심", "생애 전반 회고록", "관조적 에세이체"],
    },
    {
        "sequence_order": 56,
        "life_period": "adulthood",
        "title": "부모님의 늙어감",
        "content": "내 아이 키우느라 정신없던 어느 날, 문득 나의 부모님이 한없이 늙어 보이셔서 가슴이 내려앉았던 구체적인 순간(거친 손, 작아진 뒷모습 등)이 있나요?",
        "suggested_tags": ["결정적 에피소드", "가족사 및 양육기", "내밀한 고백체"],
    },
    {
        "sequence_order": 57,
        "life_period": "adulthood",
        "title": "가슴에 박힌 말",
        "content": "자녀를 키우면서, 아이가 무심코 뱉은 말 한마디나 작은 행동 하나가 내 가슴을 크게 울렸거나 남몰래 눈물짓게 했던 잊지 못할 순간이 있나요?",
        "suggested_tags": ["결정적 에피소드", "가족사 및 양육기", "내밀한 고백체"],
        "eligibility": {"field": "has_children", "requires": True},
    },
    {
        "sequence_order": 58,
        "life_period": "adulthood",
        "title": "일과 타협",
        "content": "바쁜 직장이나 생업 때문에 가족과의 중요한 약속(자녀 입학식, 소풍, 생일 등)을 챙기지 못해 두고두고 미안했던 뼈아픈 하루의 에피소드가 있으신가요?",
        "suggested_tags": ["테마-일과 삶", "3인칭 평전", "편지체"],
    },
    {
        "sequence_order": 59,
        "life_period": "adulthood",
        "title": "직업 철학",
        "content": "새로운 도전을 위해 과감히 내 사업을 시작했던 경험, 혹은 묵묵히 한 직장을 지키며 일에서 깊은 보람을 찾았던 순간 등 일터에서의 나만의 '직업 철학'이 담긴 일화를 들려주세요.",
        "suggested_tags": ["테마-직업", "비즈니스 & 리더십", "대중 강연체"],
    },
    {
        "sequence_order": 60,
        "life_period": "adulthood",
        "title": "책상 위의 사물",
        "content": "장년기 시절, 선생님의 사무실 책상 위나 작업장 한 켠에 항상 놓여 있던 물건(가족사진, 낡은 수첩, 믹스커피 등)은 무엇이었고, 그것이 어떤 위로가 되었나요?",
        "suggested_tags": ["특정 시기 집중", "3인칭 관찰자 평전", "관조적 에세이체"],
    },
    {
        "sequence_order": 61,
        "life_period": "adulthood",
        "title": "치열함의 증거",
        "content": "손잡이가 닳은 서류 가방, 굳은살 박인 손, 혹은 빛바랜 작업복처럼, 나의 치열했던 장년기 삶의 흔적이 고스란히 남아있는 '사물(또는 신체)'에 대해 묘사해 주세요.",
        "suggested_tags": ["사물 중심", "3인칭 평전", "소설적 서술체"],
    },
    {
        "sequence_order": 62,
        "life_period": "adulthood",
        "title": "고단한 퇴근길",
        "content": "고된 노동이나 야근을 마치고 돌아오던 늦은 밤, 지하철 창문이나 버스 차창에 비친 퀭한 내 얼굴을 보며 속으로 삼켰던 다짐이나 눈물의 에피소드를 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "실패와 재기의 기록", "내밀한 고백체"],
    },
    {
        "sequence_order": 63,
        "life_period": "adulthood",
        "title": "책임감과 버팀",
        "content": "가족을 건사하기 위해 내 젊은 날의 자존심을 포기해야 했던 일터에서, 억울하고 더러운 순간을 꾹 참고 버티게 해주었던 원동력이 된 구체적인 하루를 들려주세요.",
        "suggested_tags": ["특정 시기 집중", "가족사", "담담한 평어체"],
    },
    {
        "sequence_order": 64,
        "life_period": "adulthood",
        "title": "잠 못 이룬 새벽",
        "content": "사업이나 직장 일로 큰 위기를 맞아, 뜬눈으로 밤을 새우며 천장만 바라보았던 가장 길고 캄캄했던 새벽의 공기와 그때의 절망감을 묘사해 주시겠어요?",
        "suggested_tags": ["역순행적 구성", "실패와 재기의 기록", "담담한 평어체"],
    },
    {
        "sequence_order": 65,
        "life_period": "adulthood",
        "title": "아찔했던 금전적 위기",
        "content": "빚, 보증, 혹은 지인에게 빌려준 돈 때문에 밤잠을 설칠 정도로 피가 말랐던 경제적 위기의 구체적인 사건과 그 해결 과정을 들려주세요.",
        "suggested_tags": ["역순행적 구성", "비즈니스 & 리더십", "담담한 평어체"],
    },
    {
        "sequence_order": 66,
        "life_period": "adulthood",
        "title": "최악의 고비와 극복",
        "content": "IMF, 사업 실패, 혹은 개인적인 질병 등 가장 밑바닥까지 떨어졌다고 느꼈던 인생의 큰 고비와, 그 터널을 결국 어떻게 빠져나왔는지 들려주세요.",
        "suggested_tags": ["역순행적 구성", "실패와 재기의 기록", "멘토링 대담집"],
    },
    {
        "sequence_order": 67,
        "life_period": "adulthood",
        "title": "배신과 상처",
        "content": "사회생활을 하며 믿었던 사람에게 처참하게 배신당하거나 억울하게 큰 손해를 입었던 구체적인 사건이 있나요? 그 배신감을 어떻게 버텨내셨나요?",
        "suggested_tags": ["결정적 에피소드", "실패와 재기", "내밀한 고백체"],
    },
    {
        "sequence_order": 68,
        "life_period": "adulthood",
        "title": "신념과 타협",
        "content": "'이것만큼은 절대 타협할 수 없다'는 나만의 양심이나 신념을 지키기 위해, 눈앞의 금전적 이익이나 승진 기회를 과감히 포기했던 일화가 있으신가요?",
        "suggested_tags": ["결정적 에피소드", "가치관 사전", "객관적 기록체"],
    },
    {
        "sequence_order": 69,
        "life_period": "adulthood",
        "title": "짜릿한 성취",
        "content": "간절히 원하던 승진이나 큰 계약을 성사시켰던 날, 혹은 빚을 다 청산했던 날, 가장 먼저 누구에게 전화를 걸었으며 그때 어떤 대화를 나누셨나요?",
        "suggested_tags": ["결정적 에피소드", "비즈니스 & 리더십", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 70,
        "life_period": "adulthood",
        "title": "나를 위한 시간",
        "content": "쉼 없이 일하는 와중에도 온전히 나 자신을 위해 시간과 비용을 투자하며 열정을 쏟았던 특별한 취미나 배움, 그리고 그 속에서 느낀 즐거움을 들려주세요.",
        "suggested_tags": ["테마-취미", "덕업일치", "관조적 에세이체"],
    },
    {
        "sequence_order": 71,
        "life_period": "adulthood",
        "title": "평생의 은인",
        "content": "가족만큼 서로의 곁을 든든하게 지켜주었던 평생의 은인이나, 내가 가장 힘들 때 조건 없이 손을 내밀어 주었던 인연과 함께 나눈 구체적인 에피소드를 들려주세요.",
        "suggested_tags": ["테마-인연", "생애 전반 회고록", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 72,
        "life_period": "adulthood",
        "title": "중년의 우울",
        "content": "40~50대를 지나며 문득 '내 인생은 어디로 가고 있나' 하는 지독한 공허함이나 중년의 위기를 겪었던 시기와, 그것을 극복하게 해 준 작은 계기를 들려주세요.",
        "suggested_tags": ["특정 시기 집중", "실패와 재기", "내밀한 고백체"],
    },
    {
        "sequence_order": 73,
        "life_period": "adulthood",
        "title": "잊지 못할 기념일",
        "content": "마흔 살 혹은 쉰 살이 되던 해의 생일이나 결혼기념일 중, 유독 특별한 이벤트를 했거나 반대로 너무 외롭고 씁쓸하게 보냈던 구체적인 하루가 있나요?",
        "suggested_tags": ["연대기 구성", "생애 전반 회고록", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 74,
        "life_period": "adulthood",
        "title": "인생의 훈장",
        "content": "청춘을 다 바쳐 달려온 장년기를 돌아보며, 누구의 부모나 직함이 아닌 '내 이름 석 자'로 이뤄낸 가장 자랑스러운 성과(물건, 사건)와 스스로에게 해주고 싶은 위로를 들려주세요.",
        "suggested_tags": ["연대기 구성", "비즈니스 & 리더십", "편지체"],
    },
    {
        "sequence_order": 75,
        "life_period": "adulthood",
        "title": "자유 발언",
        "content": "그 외에 장년기에 땀 흘렸던 일터에서의 에피소드나 치열했던 삶의 기록 중 자서전에 꼭 남기고 싶은 공간이나 물건의 이야기가 있다면 자유롭게 말씀해 주세요.",
        "suggested_tags": ["연대기 구성", "3인칭 관찰자 평전", "담담한 평어체"],
    },
    # ---------------------------------------------------------------- #
    # 제4장. 노년기 및 현재 (은퇴~현재) — sequence_order 76~100            #
    # ---------------------------------------------------------------- #
    {
        "sequence_order": 76,
        "life_period": "senior",
        "title": "자녀 독립",
        "content": "자식들이 하나둘 자신의 삶을 찾아 독립해 나갔던 날, 텅 빈 방이나 현관을 보며 느꼈던 시원섭섭함이나 문득 찾아온 적막함 등 자녀 독립에 얽힌 일화를 들려주세요.",
        "suggested_tags": ["공간 중심", "가족사 및 양육기", "관조적 에세이체"],
        "eligibility": {"field": "has_children", "requires": True},
    },
    {
        "sequence_order": 77,
        "life_period": "senior",
        "title": "새로운 가족",
        "content": "자녀가 가정을 꾸려 며느리나 사위를 식구로 맞이했거나, 눈에 넣어도 아프지 않을 첫 손주를 품에 안았던 날의 풍경처럼 가족의 울타리가 넓어지며 겪은 에피소드를 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "가족사 및 양육기", "친근한 대화체"],
        "eligibility": {"field": "has_children", "requires": True},
    },
    {
        "sequence_order": 78,
        "life_period": "senior",
        "title": "배우자의 새로운 모습",
        "content": "나이가 들고 두 사람만 남게 되면서, 젊은 시절에는 몰랐던 배우자의 짠한 모습이나 귀여운 모습을 새롭게 발견하게 된 구체적인 일화를 들려주세요.",
        "suggested_tags": ["테마-관계", "가족사 및 양육기", "따뜻한 대화체"],
        "eligibility": {"field": "marital_status", "requires_one_of": ["married", "divorced", "widowed"]},
    },
    {
        "sequence_order": 79,
        "life_period": "senior",
        "title": "은퇴의 순간",
        "content": "평생을 바친 일터에서 물러나던 마지막 날 개인 짐을 박스에 챙겨 나오던 풍경이나, 생업의 무거운 짐을 내려놓았을 때 느꼈던 감정(후련함, 막막함)을 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "비즈니스 & 리더십", "소설적 서술체"],
    },
    {
        "sequence_order": 80,
        "life_period": "senior",
        "title": "나만의 아침 의식",
        "content": "은퇴 후 출근할 곳이 없어진 지금, 매일 아침 눈을 뜨면 가장 먼저 하는 선생님만의 구체적인 일상 의식(예: 따뜻한 차 한 잔, 신문 읽기, 화초 물 주기 등)을 묘사해 주세요.",
        "suggested_tags": ["테마-일상", "관조적 에세이체", "소설적 서술체"],
    },
    {
        "sequence_order": 81,
        "life_period": "senior",
        "title": "애착 물건",
        "content": "돋보기안경, 손때 묻은 찻잔, 혹은 가장 편안한 안락의자처럼 은퇴 후 지금 선생님의 일상에서 가장 가까이 두고 애용하는 물건 하나를 꼽고 그 이유를 소개해 주세요.",
        "suggested_tags": ["테마-사물", "관조적 에세이체", "소설적 서술체"],
    },
    {
        "sequence_order": 82,
        "life_period": "senior",
        "title": "가장 편안한 공간",
        "content": "지금까지 살아오신 수많은 집이나 동네 중에서, 눈을 감으면 가장 평안하고 돌아가고 싶은 구체적인 장소(혹은 현재 집의 특정 공간)는 어디이며, 그 이유는 무엇인가요?",
        "suggested_tags": ["공간 중심", "생애 전반 회고록", "관조적 에세이체"],
    },
    {
        "sequence_order": 83,
        "life_period": "senior",
        "title": "자연과 계절의 재발견",
        "content": "젊은 시절엔 무심코 지나쳤던 길가의 꽃이나 단풍이, 은퇴 후 어느 날 유독 가슴 시리게 아름다워 보여 한참을 멈춰 서 있었던 구체적인 기억의 하루가 있나요?",
        "suggested_tags": ["특정 시기 집중", "관조적 에세이체", "내밀한 고백체"],
    },
    {
        "sequence_order": 84,
        "life_period": "senior",
        "title": "뒤늦은 배움과 실수",
        "content": "스마트폰, 키오스크, 유튜브 등 새로운 디지털 기기를 처음 배우면서 겪었던 땀나는 실수나, 생각보다 너무 재미있어서 푹 빠졌던 소소한 에피소드가 있나요?",
        "suggested_tags": ["테마-배움", "유머러스한 풍자체", "친근한 대화체"],
    },
    {
        "sequence_order": 85,
        "life_period": "senior",
        "title": "오랜 인연과의 재회",
        "content": "바빠서 연락이 끊겼다가 우연히(혹은 수소문 끝에) 다시 만나게 된 옛 친구나 지인과 재회했던 순간, 주름진 서로의 얼굴을 마주했을 때 나누었던 대화를 들려주세요.",
        "suggested_tags": ["결정적 에피소드", "인연", "가상의 인터뷰체"],
    },
    {
        "sequence_order": 86,
        "life_period": "senior",
        "title": "노년의 우정",
        "content": "나이가 들면서 복지관, 동호회, 동네 산책길에서 우연히 만나 마음을 나누게 된 새로운 인연이나, 늦은 나이에도 허물 없이 말벗이 되어주는 친구를 사귀게 된 일화를 들려주세요.",
        "suggested_tags": ["테마-인연", "3인칭 관찰자 평전", "유머러스한 풍자체"],
    },
    {
        "sequence_order": 87,
        "life_period": "senior",
        "title": "이별과 태도",
        "content": "가까웠던 친구나 소중한 사람들과의 영원한 이별(사별)을 경험하며 장례식장에서 느꼈던 감정, 그리고 그 이별을 겪어내며 남은 삶을 대하는 태도가 어떻게 달라졌는지 들려주세요.",
        "suggested_tags": ["테마-죽음", "가치관 및 철학 사전", "내밀한 고백체"],
    },
    {
        "sequence_order": 88,
        "life_period": "senior",
        "title": "신체의 변화",
        "content": "나이가 들면서 예전 같지 않은 체력이나 낯선 신체의 변화를 체감하며 덜컥 겁이 났거나, 큰 수술이나 질병을 계기로 건강과 삶을 대하는 태도가 완전히 바뀌게 된 일화를 들려주세요.",
        "suggested_tags": ["역순행적 구성", "특정 시기 집중", "담담한 평어체"],
    },
    {
        "sequence_order": 89,
        "life_period": "senior",
        "title": "거울 앞에서의 성찰",
        "content": "어느 날 문득 거울 속에 비친 하얀 머리와 깊은 주름의 나 자신과 가만히 눈이 마주쳤을 때, 살아온 세월을 향해 속으로 어떤 인사를 건네셨나요?",
        "suggested_tags": ["역순행적 구성", "과거의 나에게 건네는 편지체", "3인칭 평전"],
    },
    {
        "sequence_order": 90,
        "life_period": "senior",
        "title": "뒤늦은 취미 몰입",
        "content": "생업과 육아에서 벗어나 온전히 나에게 주어지는 시간이 많아진 후, 뒤늦게 푹 빠지게 된 취미나 배움, 혹은 나만을 위해 시간을 쓰면서 느낀 즐거움에 대한 이야기를 들려주세요.",
        "suggested_tags": ["테마-취미", "덕업일치 및 취미 몰입기", "객관적 기록체"],
    },
    {
        "sequence_order": 91,
        "life_period": "senior",
        "title": "소소한 행복",
        "content": "거창한 사건은 없더라도, 요즘 하루 일과 중 가장 마음이 편안해지거나 소소한 행복을 느끼는 특정 시간과 장소(예: 이른 아침의 산책길, 노인정 화투 등)의 풍경을 들려주세요.",
        "suggested_tags": ["공간 중심", "관조적 에세이체", "소설적 서술체"],
    },
    {
        "sequence_order": 92,
        "life_period": "senior",
        "title": "기억을 부르는 노래",
        "content": "요즘 길을 걷다 우연히 어떤 옛날 노래나 특정한 냄새를 맡았을 때, 순식간에 젊은 시절의 어느 장소로 훅 빨려 들어가는 듯한 아련한 경험을 한 적이 있나요?",
        "suggested_tags": ["결정적 에피소드", "생애 전반 회고록", "소설적 서술체"],
    },
    {
        "sequence_order": 93,
        "life_period": "senior",
        "title": "물건 비우기",
        "content": "나이가 들면서 집안 살림이나 옛날 짐들을 과감히 버리고 정리했던 날, 낡은 물건들을 쓰레기봉투에 담으며 느꼈던 시원섭섭한 감정과 비워냄에 대한 철학을 들려주세요.",
        "suggested_tags": ["역순행적 구성", "가치관 및 철학 사전", "내밀한 고백체"],
    },
    {
        "sequence_order": 94,
        "life_period": "senior",
        "title": "과거의 나 칭찬",
        "content": "지나온 험난했던 날들 중, '그때 정말 도망치고 싶었는데 포기하지 않고 잘 버텼다'며 내 스스로의 어깨를 토닥여주고 싶은 구체적인 시기와 사건은 언제인가요?",
        "suggested_tags": ["역순행적 구성", "실패와 재기의 기록", "과거의 나에게 건네는 편지체"],
    },
    {
        "sequence_order": 95,
        "life_period": "senior",
        "title": "부질없는 것들",
        "content": "젊은 시절에는 목숨처럼 아끼고 집착했지만, 나이가 든 지금 돌아보니 참 부질없었다고 느껴지는 물건이나 가치(돈, 겉치레 등)가 있나요? 어떤 일을 겪고 이를 깨달으셨나요?",
        "suggested_tags": ["테마-가치관", "가치관 및 철학 사전", "대중 강연체"],
    },
    {
        "sequence_order": 96,
        "life_period": "senior",
        "title": "비워낸 관계",
        "content": "살아오며 맺었던 수많은 관계 중, 나이가 들면서 자연스럽게 혹은 의도적으로 비워내고 정리하게 된 관계들이 있나요? 마음을 비우고 사람에 대한 기대를 내려놓게 된 계기를 들려주세요.",
        "suggested_tags": ["테마-관계", "가치관 및 철학 사전", "관조적 에세이체"],
    },
    {
        "sequence_order": 97,
        "life_period": "senior",
        "title": "전수하고 싶은 유산",
        "content": "자녀나 손주들에게 재산 외에, '이것만큼은 내 손맛(혹은 기술, 삶의 지혜)을 꼭 전수해주고 싶다'고 생각하는 찌개 레시피나 소소한 생활의 팁이 있다면 무엇인가요?",
        "suggested_tags": ["가족사 및 양육기", "멘토링 대담집", "친근한 대화체"],
    },
    {
        "sequence_order": 98,
        "life_period": "senior",
        "title": "가족에게 남기는 말",
        "content": "지나온 인생 전체를 가만히 돌아보며, 지금 곁에 있는 소중한 사람들(자녀, 손주 등)이나 내 기록을 읽을 누군가에게 반드시 남겨주고 싶은 단 하나의 당부나 지혜를 들려주세요.",
        "suggested_tags": ["연대기 구성(에필로그)", "멘토링 대담집", "친근한 대화체"],
    },
    {
        "sequence_order": 99,
        "life_period": "senior",
        "title": "마지막 식사",
        "content": "만약 사랑하는 가족들을 위해 이 세상에서 마지막으로 딱 한 끼의 식사를 차려줄 수 있다면, 어떤 메뉴를 만들어 어떤 이야기를 나누며 먹고 싶으신가요?",
        "suggested_tags": ["결정적 에피소드", "멘토링 대담집", "친근한 대화체"],
    },
    {
        "sequence_order": 100,
        "life_period": "senior",
        "title": "자유 발언",
        "content": "마지막으로, 노년기의 일상이나 자서전의 끝자락에 꼭 덧붙이고 싶은 특별한 에피소드, 사물, 장소, 남기고 싶은 말이 있다면 자유롭게 말씀해 주세요.",
        "suggested_tags": ["자유 구성", "생애 전반 회고록", "가상의 인터뷰체"],
    },
]

# sequence_order(1~100, 유니크) → 해당 질문 dict. 런타임에 DB/Mock에서 조회한
# QuestionRecord.sequence_order로 이 원본 데이터(특히 suggested_tags)를 다시 찾아오는
# 유일한 안전한 경로다 — 시드 시 각 질문에 새 UUID가 매번 발급되므로 id로는 되짚어올
# 수 없고, sequence_order만이 두 시드 경로(006 마이그레이션·MockStore)와 이 파일
# 사이에서 안정적으로 일치하는 자연 키다.
QUESTION_BANK_BY_SEQUENCE: dict[int, dict] = {q["sequence_order"]: q for q in QUESTION_BANK}
