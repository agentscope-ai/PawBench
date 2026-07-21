import json
from solution import parse_script

# Read the script content from temp_script.txt
with open('temp_script.txt', 'r') as f:
    script_content = f.read()

# Parse the script content
parsed_graph = parse_script(script_content)

# Write the parsed graph to dialogue.json
with open('dialogue.json', 'w') as f:
    json.dump(parsed_graph, f, indent=4)