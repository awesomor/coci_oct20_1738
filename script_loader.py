import re

"""
script_loader.py 개선 버전
--------------------------
✅ 기존 기능 유지: load_script_lines()
✅ 신규 기능 추가: load_script_with_scenes()
   - "Scene n" 패턴 자동 감지
   - Scene별 line mapping
   - Fallback: Scene 없음 → 5줄씩 자동 분할
"""

def normalize_line(text: str) -> str:
    """특수문자, 인용부호 등 제거 및 정리"""
    text = text.strip()
    text = re.sub(r"[\"\'“”,…]", "", text)
    text = re.sub(r"「.*?」", "", text)
    return text.strip()


def load_script_lines(path="media/scripts.txt"):
    """
    ✅ 기존 함수 (절대 건드리지 않음)
    순수한 대사만 줄 단위 리스트로 반환.
    """
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()

            # Scene/제목/메타 제거
            if not line or "Scene" in line or "전체 극본" in line or "🎭" in line:
                continue

            clean = normalize_line(line)
            if clean:
                lines.append(clean)
    return lines


def load_script_with_scenes(path="media/scripts.txt"):
    """
    🎭 Scene 정보를 포함한 구조 반환:
    {
      "lines": [...],              # 기존과 동일 (전체 순서)
      "scenes": [
         {"scene": 1, "line": "..."},
         {"scene": 1, "line": "..."},
         {"scene": 2, "line": "..."}
      ],
      "scene_count": 5
    }
    """

    raw_lines = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw_lines.append(raw.rstrip("\n"))

    scenes = []
    current_scene = None
    lines_only = []

    scene_pattern = re.compile(r"Scene\s+(\d+)", re.IGNORECASE)

    for raw in raw_lines:
        # Scene 헤더 감지
        scene_match = scene_pattern.search(raw)
        if scene_match:
            current_scene = int(scene_match.group(1))
            continue  # 이 라인은 콘텐츠로 사용하지 않음

        # 제목, 전체 극본, 이모지 등 무시
        if "전체 극본" in raw or "🎭" in raw:
            continue

        clean = normalize_line(raw)
        if not clean:
            continue

        # Scene 없으면 fallback으로 1부터 시작
        if current_scene is None:
            current_scene = 1

        # 저장
        lines_only.append(clean)
        scenes.append({
            "scene": current_scene,
            "line": clean
        })

    # 🎯 만약 Scene이 전혀 감지되지 않았다면 → 5줄씩 자동 Scene
    if all(s["scene"] == 1 for s in scenes):
        print("[script_loader] Scene 헤더 없음, 5줄씩 자동 Scene 분할")
        new_scenes = []
        scene_num = 1
        for i, txt in enumerate(lines_only):
            new_scenes.append({"scene": scene_num, "line": txt})
            if (i + 1) % 5 == 0:
                scene_num += 1
        scenes = new_scenes

    # 최종 Scene 개수
    scene_count = len(set(s["scene"] for s in scenes))

    return {
        "lines": lines_only,
        "scenes": scenes,
        "scene_count": scene_count
    }


# -------------------------
# 테스트 실행
# -------------------------
if __name__ == "__main__":
    data = load_script_with_scenes()
    print("Total lines:", len(data["lines"]))
    print("Total scenes:", data["scene_count"])
    for item in data["scenes"]:
        print(f"[Scene {item['scene']}] {item['line']}")
