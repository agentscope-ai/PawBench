"""Template engine for structured output generation."""

class TemplateEngine:
    def __init__(self, templates_path=None):
        self.templates_path = templates_path
    
    def render(self, template_name, data):
        return str(data)
