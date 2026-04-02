# config/parameters.py

FACADE_PARAMETERS = [
    {
        'name': 'windload',
        'display_name': 'Wind Load',
        'description': 'Wind force on facade/curtain wall including basic wind speed, design wind pressure, wind zone, terrain category, and any referenced wind standard (IS 875, ASCE 7, BS EN 1991-1-4)',
        'expected_units': ['kN/m²', 'Pa', 'kPa', 'N/m²', 'kN/m2', 'psf', 'm/s', 'km/h'],
        'value_type': 'text',
        'search_keywords': ['wind load', 'wind pressure', 'design wind', 'wind force', 'windload', 'basic wind speed', 'design wind pressure', 'wind zone', 'terrain category', 'IS 875', 'ASCE 7', 'EN 1991', 'wind speed', 'wind coefficient', 'Vb', 'Vz'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'water_tightness',
        'display_name': 'Water Tightness',
        'description': 'Resistance to water penetration through facade joints and glazing including test pressure, performance class (E0–E4), and referenced standard (EN 12154/12155, AAMA, CWCT)',
        'expected_units': ['Pa', 'hPa', 'class E0', 'class E1', 'class E2', 'class E3', 'class E4', 'kPa'],
        'value_type': 'text',
        'search_keywords': ['water tightness', 'water resistance', 'water penetration', 'watertight', 'water infiltration', 'EN 12155', 'EN 12154', 'water test pressure', 'watertightness class', 'static water pressure', 'dynamic water pressure', 'AAMA 501', 'CWCT'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'air_permeability',
        'display_name': 'Air Permeability',
        'description': 'Air leakage rate through facade under pressure including leakage class (A1–A4), test pressure, leakage rate, and referenced standard (EN 12152/12153, AAMA, ASTM E283)',
        'expected_units': ['m³/h·m²', 'm3/h.m2', 'class A1', 'class A2', 'class A3', 'class A4', 'm³/(h·m)', 'cfm/ft²'],
        'value_type': 'text',
        'search_keywords': ['air permeability', 'air leakage', 'air infiltration', 'air tightness', 'EN 12153', 'EN 12152', 'air leakage class', 'infiltration rate', 'ASTM E283', 'AAMA 501', 'air test pressure', 'air infiltration rate'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'seismic',
        'display_name': 'Seismic Performance',
        'description': 'Seismic design parameters including seismic zone, zone factor (Z), importance factor (I), response reduction factor (R), inter-storey drift, and ground acceleration for the facade or structure',
        'expected_units': ['mm', '%', 'g', 'mm/m', 'zone', 'dimensionless'],
        'value_type': 'text',
        'search_keywords': ['seismic', 'earthquake', 'seismic zone', 'zone factor', 'importance factor', 'response reduction', 'inter-storey drift', 'interstory drift', 'seismic load', 'ground acceleration', 'PGA', 'IS 1893', 'seismic coefficient', 'Z factor', 'seismic design'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'acoustic_rating',
        'display_name': 'Acoustic Rating',
        'description': 'Sound insulation performance of the facade including weighted sound reduction index (Rw), adaptation terms (C, Ctr), STC, OITC ratings, and any referenced acoustic standard',
        'expected_units': ['dB', 'Rw', 'Rw+Ctr', 'Rw+C', 'STC', 'OITC', 'dB(A)'],
        'value_type': 'text',
        'search_keywords': ['acoustic', 'sound insulation', 'noise reduction', 'sound rating', 'Rw', 'sound transmission', 'STC', 'OITC', 'sound reduction index', 'weighted sound', 'Rw+Ctr', 'Rw+C', 'dB', 'noise criterion', 'acoustic performance', 'sound attenuation'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'u_value',
        'display_name': 'U-Value',
        'description': 'Thermal transmittance of the facade assembly including overall U-value (Uw), glass U-value (Ug), frame U-value (Uf), centre-pane U-value, solar heat gain coefficient (SHGC), and referenced thermal standard',
        'expected_units': ['W/m²K', 'W/m2K', 'W/(m²·K)', 'BTU/h·ft²·°F'],
        'value_type': 'text',
        'search_keywords': ['u value', 'u-value', 'thermal transmittance', 'heat transfer coefficient', 'thermal performance', 'Uw', 'Uf', 'Ug', 'centre pane', 'overall u-value', 'SHGC', 'solar heat gain', 'thermal insulation', 'EN ISO 10077', 'g-value'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'glass_thickness_openable',
        'display_name': 'Glass Thickness (Openable)',
        'description': 'Glass specification for openable/operable panels including thickness, makeup (e.g. 6+12+6 mm IGU), glass type (toughened, laminated, heat-strengthened), coating, and any performance class',
        'expected_units': ['mm'],
        'value_type': 'text',
        'search_keywords': ['openable glass', 'opening panel glass', 'casement glass thickness', 'operable glazing', 'vent glass', 'opening light glass', 'glazing thickness', 'glass makeup', 'laminated glass', 'toughened glass', 'IGU', 'insulating glass unit', 'glass specification', 'glass buildup', 'vent panel'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'bmu_load',
        'display_name': 'BMU Load',
        'description': 'Building Maintenance Unit load on the facade or roof anchorage points including cradle load, gondola load, davit load, monorail load, and fixing/anchorage capacity',
        'expected_units': ['kN', 'kN/m', 'kg', 'kN/m²', 'N'],
        'value_type': 'text',
        'search_keywords': ['BMU', 'building maintenance unit', 'cradle load', 'gondola load', 'facade access', 'maintenance unit load', 'BMU anchorage', 'access equipment', 'suspended platform', 'davit', 'monorail', 'outrigger load', 'window cleaning', 'abseil', 'rope access'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'no_of_barriers',
        'display_name': 'No. of Barriers',
        'description': 'Number and type of barriers, handrails, balustrades, guard rails, or parapets required on the facade or building including height, load requirements, and location',
        'expected_units': ['nos', 'number', 'qty'],
        'value_type': 'text',
        'search_keywords': ['number of barriers', 'no. of barriers', 'handrail', 'balustrade', 'barrier count', 'guard rail', 'safety barrier', 'glass balustrade', 'railing', 'parapet', 'fall protection', 'barrier height', 'balustrade load'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'stack_height',
        'display_name': 'Stack Height',
        'description': 'Vertical stack joint height, floor-to-floor height, or storey height for the building or facade system including typical and atypical floor heights',
        'expected_units': ['mm', 'm'],
        'value_type': 'text',
        'search_keywords': ['stack height', 'storey height', 'floor height', 'stack joint height', 'vertical stack', 'floor-to-floor height', 'floor to floor', 'typical floor height', 'ceiling height', 'slab to slab', 'inter-floor height', 'structural grid'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'vertical_stack_movement',
        'display_name': 'Vertical Stack Movement',
        'description': 'Allowable vertical movement or deflection in facade stack joints due to structural, thermal, creep, or shrinkage movement including slab deflection limits',
        'expected_units': ['mm', '%'],
        'value_type': 'text',
        'search_keywords': ['vertical stack movement', 'vertical movement', 'stack deflection', 'vertical deflection', 'storey drift vertical', 'axial movement', 'slab deflection', 'differential movement', 'structural movement', 'creep', 'shrinkage', 'vertical tolerance', 'long term deflection'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'signage_load',
        'display_name': 'Signage Load',
        'description': 'Load imposed by signage elements attached to or integrated into the facade including sign weight, fixing load, illuminated sign load, and any dynamic or wind load on signage',
        'expected_units': ['kN', 'kN/m²', 'kg', 'N', 'kN/m'],
        'value_type': 'text',
        'search_keywords': ['signage load', 'sign load', 'signage weight', 'facade signage', 'signage fixing', 'external signage', 'facade mounted sign', 'illuminated sign', 'sign fixing', 'sign weight', 'advertisement load', 'hoarding load'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'horizontal_movement',
        'display_name': 'Horizontal Movement',
        'description': 'Allowable horizontal movement or inter-storey drift of the facade system including wind drift, seismic drift ratio, racking movement, and lateral deflection limits (e.g. H/200, H/300)',
        'expected_units': ['mm', '%', 'mm/m', 'H/xxx'],
        'value_type': 'text',
        'search_keywords': ['horizontal movement', 'inter-storey drift', 'lateral movement', 'horizontal deflection', 'sway', 'lateral drift', 'racking movement', 'storey drift', 'lateral deflection', 'racking', 'interstory drift ratio', 'wind drift', 'drift limit', 'H/200', 'H/300', 'H/400', 'drift ratio'],
        'extraction_strategy': 'llm_with_context'
    },
]
