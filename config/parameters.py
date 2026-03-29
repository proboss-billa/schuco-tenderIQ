# config/parameters.py

FACADE_PARAMETERS = [
    {
        'name': 'windload',
        'display_name': 'Windload',
        'description': 'Wind force on facade/curtain wall',
        'expected_units': ['kN/m²', 'Pa', 'kN/m2', 'N/m²'],
        'value_type': 'numeric',
        'search_keywords': ['wind load', 'wind pressure', 'design wind', 'wind force', 'windload'],
        'extraction_strategy': 'llm_with_context'
    },
    # {
    #     'name': 'glass_thickness_vision',
    #     'display_name': 'Glass Thickness Vision & Spandrel',
    #     'description': 'Thickness of vision and spandrel glass',
    #     'expected_units': ['mm'],
    #     'value_type': 'numeric',
    #     'search_keywords': ['glass thickness', 'vision glass', 'spandrel glass', 'glazing thickness'],
    #     'extraction_strategy': 'llm_with_context'
    # },
    # {
    #     'name': 'glass_thickness_openable',
    #     'display_name': 'Glass Thickness Openable',
    #     'description': 'Thickness of glass in openable panels',
    #     'expected_units': ['mm'],
    #     'value_type': 'numeric',
    #     'search_keywords': ['openable glass', 'opening panel glass', 'casement glass thickness'],
    #     'extraction_strategy': 'llm_with_context'
    # },
    # # ... (continue for all 25 parameters)
    # {
    #     'name': 'water_tightness',
    #     'display_name': 'Water Tightness',
    #     'description': 'Resistance to water penetration',
    #     'expected_units': ['Pa'],
    #     'value_type': 'numeric',
    #     'search_keywords': ['water tightness', 'water resistance', 'water penetration', 'watertight'],
    #     'extraction_strategy': 'llm_with_context'
    # },
    # {
    #     'name': 'air_permeability',
    #     'display_name': 'Air Permeability',
    #     'description': 'Air leakage through facade',
    #     'expected_units': ['m³/h·m²', 'm3/h.m2', 'class A1', 'class A2', 'class A3', 'class A4'],
    #     'value_type': 'text',  # Can be numeric or class rating
    #     'search_keywords': ['air permeability', 'air leakage', 'air infiltration', 'air tightness'],
    #     'extraction_strategy': 'llm_with_context'
    # },
    # {
    #     'name': 'u_value',
    #     'display_name': 'U-Value',
    #     'description': 'Thermal transmittance',
    #     'expected_units': ['W/m²K', 'W/m2K'],
    #     'value_type': 'numeric',
    #     'search_keywords': ['u value', 'u-value', 'thermal transmittance', 'heat transfer coefficient'],
    #     'extraction_strategy': 'llm_with_context'
    # },
    # {
    #     'name': 'acoustic_rating',
    #     'display_name': 'Acoustic',
    #     'description': 'Sound insulation performance',
    #     'expected_units': ['dB', 'db'],
    #     'value_type': 'numeric',
    #     'search_keywords': ['acoustic', 'sound insulation', 'noise reduction', 'sound rating', 'Rw'],
    #     'extraction_strategy': 'llm_with_context'
    # }
    # # ... (continue for remaining parameters)
]

# Total: 25 parameters as defined in user requirements