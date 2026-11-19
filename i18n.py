# -*- coding: utf-8 -*-
"""中/日/英/韩 四语翻译表。

用法：tr("保存为 .mod...    Ctrl+S") —— 没有对应翻译时原样返回中文，
所以界面文案可以逐步补，不会因为缺词而崩。

键 = 中文原文（等于默认语言），值是 {lang: 译文}。
"""

LANGS = ("zh", "ja", "en", "ko")
LANG_NAMES = {"zh": "中文", "ja": "日本語", "en": "English", "ko": "한국어"}

_LANG = "zh"


def set_lang(code):
    global _LANG
    _LANG = code if code in LANGS else "zh"


def get_lang():
    return _LANG


D = {
    # ---------------- 菜单栏
    "文件": {"ja": "ファイル", "en": "File", "ko": "파일"},
    "视图": {"ja": "ビュー", "en": "View", "ko": "보기"},
    "部件": {"ja": "パーツ", "en": "Part", "ko": "파트"},
    "关于": {"ja": "情報", "en": "About", "ko": "정보"},
    "打开 .mod...": {"ja": ".mod を開く...", "en": "Open .mod...", "ko": ".mod 열기..."},
    "打开 OBJ...": {"ja": "OBJ を開く...", "en": "Open OBJ...", "ko": "OBJ 열기..."},
    "打开贴图...": {"ja": "テクスチャを開く...", "en": "Open Texture...", "ko": "텍스처 열기..."},
    "重置视图": {"ja": "ビューをリセット", "en": "Reset View", "ko": "뷰 초기화"},
    "保存截图...": {"ja": "スクリーンショット保存...", "en": "Save Screenshot...",
                    "ko": "스크린샷 저장..."},
    "导出 OBJ...": {"ja": "OBJ を書き出す...", "en": "Export OBJ...", "ko": "OBJ 내보내기..."},
    "保存为 .mod...    Ctrl+S": {"ja": ".mod として保存...    Ctrl+S",
                                 "en": "Save as .mod...    Ctrl+S",
                                 "ko": ".mod로 저장...    Ctrl+S"},
    "导入动画（保存时一并写回）": {"ja": "アニメーションを読み込む（保存時に書き戻す）",
                                 "en": "Import animations (write back on save)",
                                 "ko": "애니메이션 가져오기 (저장 시 함께 기록)"},
    "动画未导入": {"ja": "アニメーション未読み込み", "en": "Animations not imported",
                   "ko": "애니메이션을 가져오지 않음"},
    "（未导入）": {"ja": "（未読み込み）", "en": " (not imported)", "ko": " (가져오지 않음)"},
    "动画": {"ja": "アニメーション", "en": "Animation", "ko": "애니메이션"},
    "动画时间线": {"ja": "アニメーションタイムライン", "en": "Animation Timeline",
                   "ko": "애니메이션 타임라인"},
    "导入动画？": {"ja": "アニメーションを読み込みますか？", "en": "Import animations?",
                   "ko": "애니메이션을 가져올까요?"},
    "导入动画": {"ja": "アニメーションを読み込む", "en": "Import animations",
                 "ko": "애니메이션 가져오기"},
    "只导入静态": {"ja": "静止ポーズのみ", "en": "Static pose only",
                   "ko": "정지 자세만"},
    "记住这个选择，以后不再询问": {"ja": "今後は確認しない", "en": "Remember this choice, don't ask again",
                                   "ko": "다시 묻지 않기"},
    "首选项...": {"ja": "環境設定...", "en": "Preferences...", "ko": "환경설정..."},
    "退出": {"ja": "終了", "en": "Exit", "ko": "종료"},
    "高质量（清晰，旋转会卡）": {"ja": "高画質（くっきり、回転は重い）",
                               "en": "High (sharp, rotation may stutter)",
                               "ko": "높음 (선명, 회전 시 끊김)"},
    "均衡（默认）": {"ja": "バランス（既定）", "en": "Balanced (default)",
                     "ko": "균형 (기본)"},
    "低（流畅，旋转模糊）": {"ja": "低画質（滑らか、回転はぼやける）",
                            "en": "Low (smooth, blurry while rotating)",
                            "ko": "낮음 (부드러움, 회전 시 흐림)"},
    "基面网格": {"ja": "グリッド", "en": "Grid", "ko": "그리드"},
    "正交视图（点右下角坐标球也能切）": {
        "ja": "正投影表示（右下の座標球クリックでも切替）",
        "en": "Orthographic view (or click the corner axis gizmo)",
        "ko": "정투영 보기 (오른쪽 아래 좌표 구 클릭으로도 전환)"},
    "变换工具": {"ja": "トランスフォームツール", "en": "Transform tool",
                 "ko": "변형 도구"},
    "视图": {"ja": "ビュー", "en": "View", "ko": "보기"},
    "3D 视图": {"ja": "3D ビュー", "en": "3D View", "ko": "3D 뷰"},
    "UV 视图": {"ja": "UV ビュー", "en": "UV View", "ko": "UV 뷰"},
    "选择": {"ja": "選択", "en": "Select", "ko": "선택"},
    "移动": {"ja": "移動", "en": "Move", "ko": "이동"},
    "旋转": {"ja": "回転", "en": "Rotate", "ko": "회전"},
    "缩放": {"ja": "拡大縮小", "en": "Scale", "ko": "크기"},
    "变换面板    N": {"ja": "トランスフォームパネル    N",
                      "en": "Transform panel    N",
                      "ko": "변형 패널    N"},
    "设置旋转范围…": {"ja": "回転範囲を設定…", "en": "Set rotation limits...",
                      "ko": "회전 범위 설정..."},
    "清除限制": {"ja": "制限を解除", "en": "Clear limits", "ko": "제한 해제"},
    "生成 AABB（选中部件）": {"ja": "AABB を生成（選択パーツ）",
                              "en": "Generate AABB (selected parts)",
                              "ko": "AABB 생성 (선택 파트)"},
    "生成 AABB（全部网格）": {"ja": "AABB を生成（全メッシュ）",
                              "en": "Generate AABB (all meshes)",
                              "ko": "AABB 생성 (모든 메시)"},
    "生成定位器…": {"ja": "ロケーターを生成…", "en": "Create locator...",
                    "ko": "로케이터 생성..."},
    "批量生成定位器": {"ja": "ロケーターを一括生成", "en": "Create locators in batch",
                       "ko": "로케이터 일괄 생성"},
    "平台 / 武器（XML）…": {"ja": "プラットフォーム / 兵器（XML）…",
                            "en": "Platforms / Guns (XML)...",
                            "ko": "플랫폼 / 무기 (XML)..."},
    "合并网格    Ctrl+J": {"ja": "メッシュを結合    Ctrl+J", "en": "Join meshes    Ctrl+J",
                           "ko": "메시 병합    Ctrl+J"},
    "按松散块分离    P": {"ja": "ルーズパーツで分離    P", "en": "Separate loose    P",
                          "ko": "느슨한 조각 분리    P"},
    "删除选中部件（网格/定位器）    Del": {
        "ja": "選択パーツを削除（メッシュ/ロケーター）    Del",
        "en": "Delete selected (mesh / locator)    Del",
        "ko": "선택 파트 삭제 (메시/로케이터)    Del"},
    "只删定位器": {"ja": "ロケーターのみ削除", "en": "Delete locators only",
                   "ko": "로케이터만 삭제"},
    "删除选中 AABB": {"ja": "選択 AABB を削除", "en": "Delete selected AABB",
                      "ko": "선택 AABB 삭제"},
    "设置父级（最后选中者为父）": {"ja": "親を設定（最後に選んだものが親）",
                                  "en": "Set parent (last selected is parent)",
                                  "ko": "부모 설정 (마지막 선택이 부모)"},
    "解除父子级": {"ja": "親子を解除", "en": "Clear parent", "ko": "부모 해제"},
    "展开全部": {"ja": "すべて展開", "en": "Expand all", "ko": "모두 펼치기"},
    "折叠全部": {"ja": "すべて折りたたむ", "en": "Collapse all", "ko": "모두 접기"},
    "应用变换": {"ja": "トランスフォームを適用", "en": "Apply transform",
                 "ko": "변형 적용"},
    "重置变换": {"ja": "トランスフォームをリセット", "en": "Reset transform",
                 "ko": "변형 초기화"},
    "原点移到几何中心": {"ja": "原点をジオメトリ中心へ", "en": "Origin to geometry center",
                        "ko": "원점을 기하 중심으로"},

    # ---------------- 左侧面板
    "模型部件：": {"ja": "モデルパーツ：", "en": "Model parts:", "ko": "모델 파트:"},
    "折叠": {"ja": "折りたたむ", "en": "Collapse", "ko": "접기"},
    "展开": {"ja": "展開", "en": "Expand", "ko": "펼치기"},
    "搜索": {"ja": "検索", "en": "Search", "ko": "검색"},
    "全部": {"ja": "すべて", "en": "All", "ko": "전체"},
    "网格": {"ja": "メッシュ", "en": "Mesh", "ko": "메시"},
    "定位器": {"ja": "ロケーター", "en": "Locator", "ko": "로케이터"},
    "自动AABB": {"ja": "自動AABB", "en": "Auto AABB", "ko": "자동 AABB"},

    # ---------------- 变换面板
    "变换": {"ja": "トランスフォーム", "en": "Transform", "ko": "변형"},
    "位置:": {"ja": "位置:", "en": "Location:", "ko": "위치:"},
    "旋转:": {"ja": "回転:", "en": "Rotation:", "ko": "회전:"},
    "缩放:": {"ja": "拡大縮小:", "en": "Scale:", "ko": "크기:"},
    "尺寸:": {"ja": "サイズ:", "en": "Dimensions:", "ko": "크기:"},
    "仅对原点生效（网格不动）": {"ja": "原点のみ（メッシュは動かさない）",
                                "en": "Origin only (mesh stays)",
                                "ko": "원점만 (메시 고정)"},
    "XYZ 欧拉": {"ja": "XYZ オイラー", "en": "XYZ Euler", "ko": "XYZ 오일러"},

    # ---------------- 首选项
    "首选项": {"ja": "環境設定", "en": "Preferences", "ko": "환경설정"},
    "撤销": {"ja": "元に戻す", "en": "Undo", "ko": "실행 취소"},
    "可撤回次数（1~500）": {"ja": "保持する回数（1〜500）", "en": "Undo steps (1-500)",
                            "ko": "되돌리기 횟수 (1~500)"},
    "操作权限": {"ja": "操作の権限", "en": "Permissions", "ko": "작업 권한"},
    "允许删除定位器": {"ja": "ロケーターの削除を許可", "en": "Allow deleting locators",
                       "ko": "로케이터 삭제 허용"},
    "允许删除 AABB": {"ja": "AABB の削除を許可", "en": "Allow deleting AABB",
                      "ko": "AABB 삭제 허용"},
    "允许删除网格（可能导致父子级错乱，默认关闭）": {
        "ja": "メッシュの削除を許可（親子関係が壊れる可能性・既定はオフ）",
        "en": "Allow deleting meshes (may break hierarchy; off by default)",
        "ko": "메시 삭제 허용 (계층이 깨질 수 있음, 기본 꺼짐)"},
    "允许合并网格 Ctrl+J（可能打乱父子级，默认关闭）": {
        "ja": "メッシュ結合 Ctrl+J を許可（親子が乱れる可能性・既定はオフ）",
        "en": "Allow joining meshes Ctrl+J (may scramble hierarchy; off by default)",
        "ko": "메시 병합 Ctrl+J 허용 (계층이 흐트러질 수 있음, 기본 꺼짐)"},
    "允许松散分离 P（可能打乱父子级，默认关闭）": {
        "ja": "ルーズ分離 P を許可（親子が乱れる可能性・既定はオフ）",
        "en": "Allow separate loose P (may scramble hierarchy; off by default)",
        "ko": "느슨한 조각 분리 P 허용 (계층이 흐트러질 수 있음, 기본 꺼짐)"},
    "语言": {"ja": "言語", "en": "Language", "ko": "언어"},
    "恢复默认": {"ja": "既定に戻す", "en": "Restore defaults", "ko": "기본값 복원"},
    "确定": {"ja": "OK", "en": "OK", "ko": "확인"},
    "取消": {"ja": "キャンセル", "en": "Cancel", "ko": "취소"},
    "数值不对": {"ja": "数値が不正", "en": "Invalid number", "ko": "잘못된 값"},
    "可撤回次数要填整数（1~500）": {"ja": "回数は整数で入力（1〜500）",
                                    "en": "Undo steps must be an integer (1-500)",
                                    "ko": "정수를 입력하세요 (1~500)"},

    # ---------------- 旋转范围对话框
    "设置旋转范围": {"ja": "回転範囲の設定", "en": "Set rotation limits",
                     "ko": "회전 범위 설정"},
    "对选中的 {} 个部件生效": {"ja": "選択中の {} パーツに適用",
                               "en": "Applies to {} selected part(s)",
                               "ko": "선택한 {}개 파트에 적용"},
    "绕轴:": {"ja": "軸:", "en": "Axis:", "ko": "축:"},
    "最小角度:": {"ja": "最小角度:", "en": "Min angle:", "ko": "최소 각도:"},
    "最大角度:": {"ja": "最大角度:", "en": "Max angle:", "ko": "최대 각도:"},
    "最小/最大角度要填数字": {"ja": "最小/最大角度は数値で入力",
                              "en": "Min/max angle must be numbers",
                              "ko": "최소/최대 각도는 숫자여야 합니다"},

    # ---------------- 平台编辑器
    "平台 / 武器": {"ja": "プラットフォーム / 兵器", "en": "Platforms / Guns",
                    "ko": "플랫폼 / 무기"},
    "写入 XML 并保存": {"ja": "XML に書き込んで保存", "en": "Write to XML and save",
                        "ko": "XML에 쓰고 저장"},
    "重新加载": {"ja": "再読み込み", "en": "Reload", "ko": "다시 불러오기"},
    "关闭": {"ja": "閉じる", "en": "Close", "ko": "닫기"},
    "平台": {"ja": "プラットフォーム", "en": "Platforms", "ko": "플랫폼"},
    "武器": {"ja": "兵器", "en": "Guns", "ko": "무기"},
    "车辆（RPG）": {"ja": "車両（RPG）", "en": "Vehicle (RPG)", "ko": "차량 (RPG)"},

    # ---------------- 平台 / 武器编辑器
    "平台 / 武器": {"ja": "プラットフォーム / 兵器", "en": "Platforms / Guns",
                    "ko": "플랫폼 / 무기"},
    "平台（Platforms）": {"ja": "プラットフォーム（Platforms）",
                          "en": "Platforms", "ko": "플랫폼 (Platforms)"},
    "武器（Guns）": {"ja": "兵器（Guns）", "en": "Guns", "ko": "무기 (Guns)"},
    "车辆（RPG）": {"ja": "車両（RPG）", "en": "Vehicle (RPG)", "ko": "차량 (RPG)"},
    "+ 追加平台": {"ja": "+ プラットフォーム追加", "en": "+ Add platform",
                   "ko": "+ 플랫폼 추가"},
    "+ 追加武器": {"ja": "+ 兵器追加", "en": "+ Add gun", "ko": "+ 무기 추가"},
    "范围填角度（写回自动转弧度）；水平 = 绕 Z 扫，俯仰 = 绕 Y 抬": {
        "ja": "範囲は角度で入力（保存時にラジアンへ自動変換）。水平＝Z 回り、俯仰＝Y 回り",
        "en": "Enter degrees (converted to radians on save). Horizontal = yaw about Z, vertical = pitch about Y",
        "ko": "범위는 각도로 입력(저장 시 라디안 변환). 수평=Z축, 수직=Y축"},
    "第 {} 门起 {} 门": {"ja": "{} 番目から {} 門", "en": "gun {} for {}",
                        "ko": "{}번째부터 {}문"},
    "部件": {"ja": "パーツ", "en": "Part", "ko": "파트"},
    "水平转速": {"ja": "水平旋回速度", "en": "H-rot speed", "ko": "수평 회전속도"},
    "水平 Min°": {"ja": "水平 Min°", "en": "H-min°", "ko": "수평 Min°"},
    "Max°": {"ja": "Max°", "en": "Max°", "ko": "Max°"},
    "垂直转速": {"ja": "垂直旋回速度", "en": "V-rot speed", "ko": "수직 회전속도"},
    "俯仰 Min°": {"ja": "俯仰 Min°", "en": "V-min°", "ko": "종 Min°"},
    "驱动炮": {"ja": "連動砲", "en": "Guns", "ko": "연동포"},
    "和模型对照": {"ja": "モデルとの対応", "en": "vs model", "ko": "모델 대조"},
    "武器": {"ja": "兵器", "en": "Gun", "ko": "무기"},
    "装填": {"ja": "装填", "en": "Reload", "ko": "재장전"},
    "开火点": {"ja": "発砲点", "en": "Shoot point", "ko": "발사점"},
    "后坐部件": {"ja": "反動パーツ", "en": "Recoil part", "ko": "반동 파트"},
    "后坐长": {"ja": "反動長", "en": "Recoil len", "ko": "반동 길이"},
    "抖动°": {"ja": "揺れ°", "en": "Shake°", "ko": "흔들림°"},
    "优先级": {"ja": "優先度", "en": "Priority", "ko": "우선순위"},
    "指向": {"ja": "参照", "en": "Target", "ko": "지시"},

    # ---------------- 常用
    "已取消": {"ja": "キャンセルしました", "en": "Cancelled", "ko": "취소됨"},
    "先选中部件": {"ja": "先にパーツを選択", "en": "Select a part first",
                   "ko": "먼저 파트를 선택하세요"},
    "删除": {"ja": "削除", "en": "Delete", "ko": "삭제"},
    "保存": {"ja": "保存", "en": "Save", "ko": "저장"},
}


def tr(text):
    """按当前语言翻译；没有词条就返回原文（中文）。"""
    if _LANG == "zh":
        return text
    item = D.get(text)
    if not item:
        return text
    return item.get(_LANG) or text
