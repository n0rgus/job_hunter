import json
import os
import subprocess
import re

def apply_edits(data, repo_dir="."):
    import os
    import subprocess
    import re

    file_path = os.path.join(repo_dir, data["file"])
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for edit in data["edits"]:
        if edit["type"] == "replace":
            idx = edit["line"] - 1
            if "original" not in edit or lines[idx].strip() == edit["original"].strip():
                lines[idx] = edit["new"] + "\\n"
        elif edit["type"] == "insert_after":
            idx = edit["line"]
            lines.insert(idx, edit["new"] + "\\n")
        elif edit["type"] == "insert_before":
            idx = edit["line"] - 1
            lines.insert(idx, edit["new"] + "\\n")
        elif edit["type"] == "replace_function":
            func_name = edit["function_name"]
            new_body = [line + "\\n" for line in edit["new_body"]]
            pattern = re.compile(rf"^\\s*def {func_name}\\(")
            in_func = False
            indent = ""
            start, end = -1, -1
            for i, line in enumerate(lines):
                if pattern.match(line):
                    start = i
                    indent = re.match(r"^(\\s*)", line).group(1)
                    in_func = True
                    continue
                if in_func and line.strip() and not line.startswith(indent + "    "):
                    end = i
                    break
            if start != -1 and end != -1:
                lines[start+1:end] = new_body

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    subprocess.run(["git", "add", data["file"]], cwd=repo_dir)
    subprocess.run(["git", "commit", "-m", data.get("commit_message", "Auto edit")], cwd=repo_dir)

    # Optional: push to GitHub (if repo remote is set)
    subprocess.run(["git", "push"], cwd=repo_dir)

    print("? Changes applied and committed.")
