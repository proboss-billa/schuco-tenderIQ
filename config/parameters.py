# config/parameters.py

FACADE_PARAMETERS = [
    {
        'name': 'windload',
        'display_name': 'Wind Load',
        'description': 'Wind force on facade/curtain wall',
        'expected_units': ['kN/m²', 'Pa', 'kN/m2', 'N/m²'],
        'value_type': 'numeric',
        'search_keywords': ['wind load', 'wind pressure', 'design wind', 'wind force', 'windload'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'water_tightness',
        'display_name': 'Water Tightness',
        'description': 'Resistance to water penetration through facade joints and glazing',
        'expected_units': ['Pa', 'class E0', 'class E1', 'class E2', 'class E3', 'class E4'],
        'value_type': 'text',
        'search_keywords': ['water tightness', 'water resistance', 'water penetration', 'watertight', 'water infiltration', 'EN 12155', 'EN 12154'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'air_permeability',
        'display_name': 'Air Permeability',
        'description': 'Air leakage rate through facade under pressure',
        'expected_units': ['m³/h·m²', 'm3/h.m2', 'class A1', 'class A2', 'class A3', 'class A4'],
        'value_type': 'text',
        'search_keywords': ['air permeability', 'air leakage', 'air infiltration', 'air tightness', 'EN 12153', 'EN 12152'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'seismic',
        'display_name': 'Seismic Performance',
        'description': 'Seismic design requirements and inter-storey drift capacity for facade',
        'expected_units': ['mm', '%', 'g', 'mm/m'],
        'value_type': 'text',
        'search_keywords': ['seismic', 'earthquake', 'inter-storey drift', 'interstory drift', 'seismic zone', 'seismic load', 'ground acceleration', 'PGA'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'acoustic_rating',
        'display_name': 'Acoustic Rating',
        'description': 'Sound insulation performance of the facade',
        'expected_units': ['dB', 'Rw', 'Rw+Ctr'],
        'value_type': 'numeric',
        'search_keywords': ['acoustic', 'sound insulation', 'noise reduction', 'sound rating', 'Rw', 'sound transmission', 'STC', 'OITC'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'u_value',
        'display_name': 'U-Value',
        'description': 'Thermal transmittance of the facade assembly',
        'expected_units': ['W/m²K', 'W/m2K', 'BTU/h·ft²·°F'],
        'value_type': 'numeric',
        'search_keywords': ['u value', 'u-value', 'thermal transmittance', 'heat transfer coefficient', 'thermal performance', 'Uw', 'Uf', 'Ug'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'glass_thickness_openable',
        'display_name': 'Glass Thickness (Openable)',
        'description': 'Thickness of glass in openable/operable panels and casements',
        'expected_units': ['mm'],
        'value_type': 'numeric',
        'search_keywords': ['openable glass', 'opening panel glass', 'casement glass thickness', 'operable glazing', 'vent glass', 'opening light glass'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'bmu_load',
        'display_name': 'BMU Load',
        'description': 'Building Maintenance Unit load on the facade or roof anchorage points',
        'expected_units': ['kN', 'kN/m', 'kg', 'kN/m²'],
        'value_type': 'numeric',
        'search_keywords': ['BMU', 'building maintenance unit', 'cradle load', 'gondola load', 'facade access', 'maintenance unit load', 'BMU anchorage'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'no_of_barriers',
        'display_name': 'No. of Barriers',
        'description': 'Number of barriers, handrails or balustrade elements required on the facade or building',
        'expected_units': ['nos', 'number', 'qty'],
        'value_type': 'numeric',
        'search_keywords': ['number of barriers', 'no. of barriers', 'handrail', 'balustrade', 'barrier count', 'guard rail', 'safety barrier'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'stack_height',
        'display_name': 'Stack Height',
        'description': 'Vertical stack joint height or storey height for facade stack joints',
        'expected_units': ['mm', 'm'],
        'value_type': 'numeric',
        'search_keywords': ['stack height', 'storey height', 'floor height', 'stack joint height', 'vertical stack', 'floor-to-floor height'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'vertical_stack_movement',
        'display_name': 'Vertical Stack Movement',
        'description': 'Allowable vertical movement or deflection in facade stack joints due to structural or thermal movement',
        'expected_units': ['mm', '%'],
        'value_type': 'numeric',
        'search_keywords': ['vertical stack movement', 'vertical movement', 'stack deflection', 'vertical deflection', 'storey drift vertical', 'axial movement'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'signage_load',
        'display_name': 'Signage Load',
        'description': 'Load imposed by signage elements attached to or integrated into the facade',
        'expected_units': ['kN', 'kN/m²', 'kg'],
        'value_type': 'numeric',
        'search_keywords': ['signage load', 'sign load', 'signage weight', 'facade signage', 'signage fixing', 'external signage'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'horizontal_movement',
        'display_name': 'Horizontal Movement',
        'description': 'Allowable horizontal movement or inter-storey drift of the facade system',
        'expected_units': ['mm', '%', 'mm/m'],
        'value_type': 'numeric',
        'search_keywords': ['horizontal movement', 'inter-storey drift', 'lateral movement', 'horizontal deflection', 'sway', 'lateral drift', 'racking movement'],
        'extraction_strategy': 'llm_with_context'
    },
]

# Total: 25 parameters as defined in user requirements