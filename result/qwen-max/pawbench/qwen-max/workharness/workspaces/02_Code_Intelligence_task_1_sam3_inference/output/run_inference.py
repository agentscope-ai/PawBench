import json
from sam3 import build_sam3_image_model, Sam3Processor
from pathlib import Path

# Initialize the SAM3 model and processor
model = build_sam3_image_model(checkpoint_path=None)  # Local simplified version does not depend on external weights
processor = Sam3Processor()

# Set the image to be processed
image_path = "sam3/assets/images/test_image.jpg"
model.set_image(image_path)

# Define a function to run prediction cases
def run_case(text=None, boxes_xywh=None, box_labels=None, confidence_threshold=0.5):
    boxes_xyxy, scores = model.predict(
        text=text,
        boxes_xywh=boxes_xywh,
        box_labels=box_labels,
        confidence_threshold=confidence_threshold
    )
    return {"boxes_xyxy": boxes_xyxy, "scores": scores}

# Test cases
cases = {
    "text_shoe": run_case(text="shoe", confidence_threshold=0.5),
    "single_box": run_case(boxes_xywh=[[480.0, 290.0, 110.0, 360.0]], confidence_threshold=0.5),
    "multi_box": run_case(boxes_xywh=[[480.0, 290.0, 110.0, 360.0], [370.0, 280.0, 115.0, 375.0]],
                          box_labels=[True, False], confidence_threshold=0.5),
    "text_box_combined": run_case(text="child", boxes_xywh=[[480.0, 290.0, 110.0, 360.0]], confidence_threshold=0.5)
}

# Build the output structure
output_data = {
    "image": Path(image_path).name,
    "image_size": list(model.image_size),
    "cases": cases
}

# Save the results to a file
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)
with (output_dir / "predictions.json").open("w") as f:
    json.dump(output_data, f, indent=2)
