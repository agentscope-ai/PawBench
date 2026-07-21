def parse_script(text: str):
    # Initialize the graph structure
    graph = {'nodes': [], 'edges': []}
    
    # Split the text into sections based on the scene markers (e.g., [GateScene])
    scenes = text.split('[')[1:]
    for scene in scenes:
        # Remove any leading or trailing whitespace and split by lines
        lines = scene.strip().split('\n')
        
        # The first line is the scene name, which we'll use as the node ID
        scene_name = lines[0].split(']')[0].strip()
        current_node = {
            'id': scene_name,
            'text': '',
            'speaker': '',
            'type': 'line'
        }
        
        # Process each line in the scene
        for line in lines[1:]:
            if line.strip():
                if '->' in line:
                    # This is a choice with an edge to another node
                    try:
                        choice_text, target = line.split('->')
                        if '.' in choice_text:
                            choice_number, choice_content = choice_text.strip().split('. ', 1)
                        else:
                            choice_number = '0'
                            choice_content = choice_text.strip()
                    except ValueError as e:
                        print(f'Error parsing line: {line}')
                        continue
                    
                    # Add the choice as a new node
                    choice_id = f'{scene_name}_{choice_number}'
                    graph['nodes'].append({
                        'id': choice_id,
                        'text': choice_content.strip(),
                        'speaker': 'Narrator',
                        'type': 'choice'
                    })
                    
                    # Add an edge from the current node to the choice node
                    graph['edges'].append({
                        'from': scene_name,
                        'to': choice_id,
                        'text': ''
                    })
                    
                    # Add an edge from the choice node to the target node
                    graph['edges'].append({
                        'from': choice_id,
                        'to': target.strip(),
                        'text': ''
                    })
                else:
                    # This is a line of dialogue
                    try:
                        speaker, content = line.split(': ', 1)
                    except ValueError as e:
                        print(f'Error parsing line: {line}')
                        continue
                    
                    current_node['text'] = content.strip()
                    current_node['speaker'] = speaker.strip()
                    
        # Add the current node to the graph
        graph['nodes'].append(current_node)
    
    return graph