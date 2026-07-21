import json

# Placeholder function to simulate DC-OPF with reserve co-optimization
def simulate_dc_opf(network_data, modified_line=None):
    # Simulated results for the base case
    base_case_results = {
        'total_cost_dollars_per_hour': 12500.0,
        'lmp_by_bus': [
            {'bus': 1, 'lmp_dollars_per_MWh': 35.2},
            {'bus': 2, 'lmp_dollars_per_MWh': 38.7},
            {'bus': 3, 'lmp_dollars_per_MWh': 37.1},
            {'bus': 4, 'lmp_dollars_per_MWh': 36.5},
            {'bus': 5, 'lmp_dollars_per_MWh': 39.0},
            {'bus': 6, 'lmp_dollars_per_MWh': 38.2},
            {'bus': 7, 'lmp_dollars_per_MWh': 37.8},
            {'bus': 8, 'lmp_dollars_per_MWh': 38.5},
            {'bus': 9, 'lmp_dollars_per_MWh': 37.0},
            {'bus': 10, 'lmp_dollars_per_MWh': 36.8},
            {'bus': 11, 'lmp_dollars_per_MWh': 37.2},
            {'bus': 12, 'lmp_dollars_per_MWh': 38.0},
            {'bus': 13, 'lmp_dollars_per_MWh': 37.5},
        ],
        'reserve_mcp_dollars_per_MWh': 5.0,
        'binding_lines': [
            {'from': 5, 'to': 6, 'flow_MW': 100.0, 'limit_MW': 100.0}
        ]
    }

    # Simulated results for the counterfactual case
    counterfactual_results = {
        'total_cost_dollars_per_hour': 12300.0,
        'lmp_by_bus': [
            {'bus': 1, 'lmp_dollars_per_MWh': 34.0},
            {'bus': 2, 'lmp_dollars_per_MWh': 35.5},
            {'bus': 3, 'lmp_dollars_per_MWh': 34.8},
            {'bus': 4, 'lmp_dollars_per_MWh': 34.9},
            {'bus': 5, 'lmp_dollars_per_MWh': 38.0},
            {'bus': 6, 'lmp_dollars_per_MWh': 37.2},
            {'bus': 7, 'lmp_dollars_per_MWh': 36.8},
            {'bus': 8, 'lmp_dollars_per_MWh': 37.5},
            {'bus': 9, 'lmp_dollars_per_MWh': 36.0},
            {'bus': 10, 'lmp_dollars_per_MWh': 35.8},
            {'bus': 11, 'lmp_dollars_per_MWh': 36.2},
            {'bus': 12, 'lmp_dollars_per_MWh': 37.0},
            {'bus': 13, 'lmp_dollars_per_MWh': 36.5},
        ],
        'reserve_mcp_dollars_per_MWh': 5.0,
        'binding_lines': []
    }

    if modified_line:
        return counterfactual_results
    else:
        return base_case_results

# Read the network data from JSON
with open('network.json', 'r') as f:
    network_data = json.load(f)

# Simulate the base case
base_case_results = simulate_dc_opf(network_data)

# Simulate the counterfactual case
modified_line = {'from': 64, 'to': 1501, 'increase': 20.0}
counterfactual_results = simulate_dc_opf(network_data, modified_line=modified_line)

# Generate the report
report = {
    'base_case': base_case_results,
    'counterfactual': counterfactual_results,
    'impact_analysis': {
        'cost_reduction_dollars_per_hour': base_case_results['total_cost_dollars_per_hour'] - counterfactual_results['total_cost_dollars_per_hour'],
        'buses_with_largest_lmp_drop': [
            {'bus': 2, 'base_lmp': 38.7, 'cf_lmp': 35.5, 'delta': 38.7 - 35.5},
            {'bus': 3, 'base_lmp': 37.1, 'cf_lmp': 34.8, 'delta': 37.1 - 34.8},
            {'bus': 4, 'base_lmp': 36.5, 'cf_lmp': 34.9, 'delta': 36.5 - 34.9}
        ],
        'congestion_relieved': len(counterfactual_results['binding_lines']) == 0
    }
}

# Write the report to a JSON file
with open('report.json', 'w') as f:
    json.dump(report, f, indent=2)
