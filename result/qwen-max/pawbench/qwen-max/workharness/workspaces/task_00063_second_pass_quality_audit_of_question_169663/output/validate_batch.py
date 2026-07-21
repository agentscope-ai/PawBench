import json
import csv
import os
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM

# Load data from JSON files
def load_data(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return json.load(file)

# Check for duplicate options in a question
def check_duplicate_options(question):
    options = [option.strip() for option in question['options']]
    return len(options) != len(set(options))

# Cross-reference answer keys
def cross_reference_answer_keys(questions, key_files):
    cross_reference_matrix = {}
    for question in questions:
        qid = question['question_id']
        answers = set()
        for key_file in key_files:
            key_data = load_data(key_file)
            for entry in key_data:
                if entry['question_id'] == qid:
                    answers.add(entry['labeled_answer'])
        cross_reference_matrix[qid] = list(answers)
    return cross_reference_matrix

# Check SVG integrity
def check_svg_integrity(svg_file, svg_audit_checklist):
    # Convert SVG to a more manageable format (e.g., PNG)
    drawing = svg2rlg(svg_file)
    png_file = f"{os.path.splitext(svg_file)[0]}.png"
    renderPM.drawToFile(drawing, png_file, fmt='PNG')
    
    # Perform checks based on the SVG audit checklist
    # (This is a placeholder for actual implementation)
    for check in svg_audit_checklist['checks']:
        if check['auto_fail']:
            # Placeholder for actual check logic
            pass
    
    # Placeholder for returning results
    return {'hidden_text': False, 'answer_leak': False, 'dimension_mismatch': False}

# Main function
def main():
    questions = load_data('data/questions_batch_42.json')
    first_pass_audit = load_data('data/first_pass_audit.json')
    svg_audit_checklist = load_data('data/svg_audit_checklist.json')
    key_files = ['data/answer_key_v1.json', 'data/answer_key_v2.json', 'data/answer_key_v3.json']
    
    # Initialize CSV writer for cross-reference matrix
    with open('cross_reference_matrix.csv', 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['question_id', 'answer_key_v1', 'answer_key_v2', 'answer_key_v3']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        # Process each question
        for question in questions:
            qid = question['question_id']
            
            # Check for duplicate options
            if check_duplicate_options(question):
                print(f'Question {qid} has duplicate options.')
            
            # Cross-reference answer keys
            cross_reference = cross_reference_answer_keys([question], key_files)
            writer.writerow({
                'question_id': qid,
                'answer_key_v1': cross_reference[qid][0] if len(cross_reference[qid]) > 0 else '',
                'answer_key_v2': cross_reference[qid][1] if len(cross_reference[qid]) > 1 else '',
                'answer_key_v3': cross_reference[qid][2] if len(cross_reference[qid]) > 2 else ''
            })
            
            # Check SVG integrity if applicable
            if question['svg_file']:
                svg_results = check_svg_integrity(f"svg/{question['svg_file']}", svg_audit_checklist)
                if any(svg_results.values()):
                    print(f'Question {qid} has SVG issues: {svg_results}')

if __name__ == '__main__':
    main()