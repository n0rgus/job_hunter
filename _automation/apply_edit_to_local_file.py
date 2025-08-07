import json
import os
import subprocess
import re

def apply_edits(json_path, repo_dir="."):
    with open(json_path, "r") as f:
        data = json.load(f)

    filepath = os.path.join(repo_dir, data["file"])
    with open(filepath, "r") as f:
        lines = f.readlines()

    for edit in data["edits"]:
        if edit["type"] == "replace":
            index = edit["line"] - 1
            if lines[index].strip() == edit["original"].strip():
                lines[index] = edit["new"] + "\n"
            else:
                print(f"?? Line {edit['line']} mismatch, skipping.")
        elif edit["type"] == "insert_after":
            index = edit["line"]
            lines.insert(index, edit["new"] + "\n")
        elif edit["type"] == "replace_function":
            func_name = edit["function_name"]
            new_body = [line + "\n" for line in edit["new_body"]]
            pattern = re.compile(rf"^\s*def {func_name}\(")
            in_func = False
            indent = ""
            start, end = -1, -1
            for i, line in enumerate(lines):
                if pattern.match(line):
                    start = i
                    indent = re.match(r"^(\s*)", line).group(1)
                    in_func = True
                    continue
                if in_func:
                    if line.strip() and not line.startswith(indent + "    "):
                        end = i
                        break
            if start != -1 and end != -1:
                lines[start+1:end] = new_body

    with open(filepath, "w") as f:
        f.writelines(lines)

    # Commit changes locally
    subprocess.run(["git", "add", data["file"]], cwd=repo_dir)
    subprocess.run(["git", "commit", "-m", data["commit_message"]], cwd=repo_dir)

    # Optional: push to GitHub (if repo remote is set)
    subprocess.run(["git", "push"], cwd=repo_dir)

    print("? Changes applied and committed.")

# Example usage
if __name__ == "__main__":
    apply_edits("changeset.json", repo_dir="/path/to/your/repo")
