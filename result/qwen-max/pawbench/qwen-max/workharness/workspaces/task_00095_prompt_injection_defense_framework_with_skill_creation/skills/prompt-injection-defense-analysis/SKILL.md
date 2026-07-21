```yaml
title: Prompt Injection Defense Analysis
trigger: prompt injection defense analysis
requires:
  - context: code
  - context: logs
  - context: incident_report
provides:
  - analysis: prompt_injection_defense
  - report: prompt_injection_defense_report
on_load:
  - skill.load_prompt_injection_patterns()
on_run:
  - skill.analyze_prompt_injection(context)
```
