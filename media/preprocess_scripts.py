import re

def load_script_by_lines(path):
    lines = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            # 1) 불필요한 제목/Scene 제거
            if "Scene" in line or "전체 극본" in line or "🎭" in line:
                continue

            # 2) 특수문자 제거
            clean = re.sub(r"[\"\'“”,…]", "", line)  # 따옴표, 말줄임 제거
            clean = re.sub(r"「.*?」", "", clean)     # 제목 따옴표 제거
            clean = clean.strip()

            # 3) 빈 줄 제거
            if clean:
                lines.append(clean)

    return lines

# 테스트
if __name__ == "__main__":
    lines = load_script_by_lines("scripts.txt")
    for i, l in enumerate(lines):
        print(f"{i}: {l}")
