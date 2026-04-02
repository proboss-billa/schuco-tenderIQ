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
    {
        'name': 'deflection_limit',
        'display_name': 'Deflection Limit',
        'description': 'Allowable deflection limits for facade mullions, transoms, and structural members under wind load, expressed as a fraction of span (e.g. L/150, L/175, L/200) or absolute value in mm',
        'expected_units': ['mm', 'L/150', 'L/175', 'L/200', 'L/250', 'L/300', 'span/xxx'],
        'value_type': 'text',
        'search_keywords': ['deflection limit', 'maximum deflection', 'allowable deflection', 'span deflection', 'L/175', 'L/200', 'L/150', 'L/300', 'mullion deflection', 'transom deflection', 'member deflection', 'serviceability limit', 'deflection criteria', 'out of plane deflection', 'span/200'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'fire_rating',
        'display_name': 'Fire Rating',
        'description': 'Fire resistance or fire spread performance requirement for the facade system including integrity (E), insulation (I), radiation (W) class, duration in minutes, and any referenced fire standard (BS EN 13501, IS 3809)',
        'expected_units': ['minutes', 'EI 60', 'EI 90', 'EI 120', 'E 30', 'E 60', 'Class A', 'Class B'],
        'value_type': 'text',
        'search_keywords': ['fire rating', 'fire resistance', 'fire performance', 'fire spread', 'fire integrity', 'fire insulation', 'EI 60', 'EI 90', 'EI 120', 'E 30', 'E 60', 'fire class', 'EN 13501', 'fire test', 'combustibility', 'non-combustible', 'limited combustibility', 'fire stop', 'fire barrier', 'BS 476', 'IS 3809'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'solar_factor',
        'display_name': 'Solar Factor / g-Value / SHGC',
        'description': 'Solar heat gain through the glazing including solar factor (g-value), solar heat gain coefficient (SHGC), total solar energy transmittance, shading coefficient, and any referenced standard',
        'expected_units': ['dimensionless', '%', 'g-value', 'SHGC', 'SC'],
        'value_type': 'text',
        'search_keywords': ['solar factor', 'g-value', 'SHGC', 'solar heat gain', 'solar transmittance', 'shading coefficient', 'total solar energy transmittance', 'solar gain', 'solar control', 'g value', 'solar heat gain coefficient', 'EN 410', 'ASHRAE', 'solar radiation'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'visible_light_transmittance',
        'display_name': 'Visible Light Transmittance (VLT)',
        'description': 'Visible light transmittance through the glazing as a percentage or decimal, including any minimum or maximum requirement and referenced standard',
        'expected_units': ['%', 'dimensionless'],
        'value_type': 'text',
        'search_keywords': ['visible light transmittance', 'VLT', 'light transmittance', 'daylight factor', 'visible transmittance', 'light transmission', 'Tv', 'luminous transmittance', 'glass transparency', 'EN 410', 'visible light factor'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'glass_thickness_fixed',
        'display_name': 'Glass Thickness (Fixed)',
        'description': 'Glass specification for fixed/non-openable panels including thickness, makeup (e.g. 8+16+8 mm IGU, 10mm toughened + 1.52PVB + 10mm toughened laminated), glass type, coating, and any performance class',
        'expected_units': ['mm'],
        'value_type': 'text',
        'search_keywords': ['fixed glass', 'fixed glazing', 'non-openable glass', 'vision glass', 'fixed panel glass', 'fixed light glass', 'IGU makeup', 'insulating glass unit', 'double glazed', 'triple glazed', 'laminated glass fixed', 'glass specification fixed', 'toughened glass fixed', 'glass build-up', 'glass schedule'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'impact_resistance',
        'display_name': 'Impact Resistance',
        'description': 'Impact resistance requirements for the facade including soft body impact (pendulum test), hard body impact, drop height, energy level, and referenced standard (EN 12600, CWCT, BS 8200)',
        'expected_units': ['J', 'kg·m', 'class', 'drop height mm'],
        'value_type': 'text',
        'search_keywords': ['impact resistance', 'impact test', 'soft body impact', 'hard body impact', 'pendulum test', 'EN 12600', 'ball drop test', 'impact class', 'impact energy', 'impact load', 'accidental impact', 'BS 8200', 'CWCT', 'glass impact', 'facade impact'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'facade_system_type',
        'display_name': 'Facade System Type',
        'description': 'Type of facade or curtain wall system including unitized, stick-built (site-assembled), semi-unitized, panelised, rainscreen, ventilated, pressure-equalised, or drained system',
        'expected_units': ['type', 'system'],
        'value_type': 'text',
        'search_keywords': ['unitized', 'stick system', 'stick built', 'site assembled', 'semi-unitized', 'panelised', 'curtain wall system', 'rainscreen', 'ventilated facade', 'pressure equalised', 'drained joint', 'facade system', 'cladding system', 'glazing system', 'system type', 'facade type'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'warranty',
        'display_name': 'Warranty Period',
        'description': 'Warranty requirements for the facade system including product warranty, system warranty, performance warranty, coating warranty, glass warranty, and duration in years',
        'expected_units': ['years', 'yr'],
        'value_type': 'text',
        'search_keywords': ['warranty', 'guarantee', 'product warranty', 'system warranty', 'performance warranty', 'coating warranty', 'glass warranty', 'warranty period', 'defects liability', 'DLP', 'defect notification period', 'warranty duration', 'year warranty', 'yr guarantee'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'testing_requirements',
        'display_name': 'Testing & Mock-up Requirements',
        'description': 'Testing and mock-up requirements for the facade system including prototype/mock-up testing, field testing, laboratory testing, test standards, test panel size, and pre-installation testing requirements',
        'expected_units': ['text', 'standard'],
        'value_type': 'text',
        'search_keywords': ['mock-up', 'mockup', 'prototype test', 'field test', 'laboratory test', 'performance testing', 'facade test', 'curtain wall test', 'test panel', 'hose test', 'AAMA 501', 'CWCT', 'pre-installation test', 'site test', 'testing requirement', 'full scale test', 'sample panel'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'slab_edge_deflection',
        'display_name': 'Slab Edge Deflection',
        'description': 'Allowable deflection or rotation at the slab edge that the facade fixing must accommodate including long-term deflection, construction tolerance, and slab camber',
        'expected_units': ['mm', '%', 'degrees'],
        'value_type': 'text',
        'search_keywords': ['slab deflection', 'slab edge deflection', 'floor deflection', 'slab rotation', 'concrete deflection', 'long term deflection', 'slab camber', 'dead load deflection', 'live load deflection', 'slab tolerance', 'structural deflection', 'floor slab deflection', 'slab edge movement'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'thermal_movement',
        'display_name': 'Thermal Movement',
        'description': 'Thermal expansion and contraction movement that the facade system must accommodate including temperature range, design temperature difference, thermal coefficient, and movement at joints',
        'expected_units': ['mm', '°C', 'mm/m·°C'],
        'value_type': 'text',
        'search_keywords': ['thermal movement', 'thermal expansion', 'thermal contraction', 'temperature range', 'design temperature', 'coefficient of thermal expansion', 'thermal cycling', 'thermal stress', 'temperature differential', 'thermal accommodation', 'expansion joint', 'thermal joint', 'ambient temperature'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'facade_dead_load',
        'display_name': 'Facade Dead Load / Self Weight',
        'description': 'Dead load or self-weight of the facade system per unit area or per linear metre, used for structural design of fixings and supporting structure',
        'expected_units': ['kN/m²', 'kN/m', 'kg/m²', 'N/m²'],
        'value_type': 'text',
        'search_keywords': ['facade dead load', 'cladding dead load', 'self weight', 'facade weight', 'curtain wall weight', 'dead load facade', 'facade self weight', 'cladding weight', 'glass dead load', 'facade load', 'system weight', 'panel weight', 'unit weight'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'sustainability_rating',
        'display_name': 'Sustainability / Green Rating',
        'description': 'Sustainability or green building rating requirements for the facade including LEED credits, BREEAM rating, GRIHA points, energy efficiency target, and any environmental certification requirement',
        'expected_units': ['rating', 'points', 'level'],
        'value_type': 'text',
        'search_keywords': ['LEED', 'BREEAM', 'GRIHA', 'green building', 'sustainability', 'energy efficiency', 'environmental rating', 'green rating', 'carbon footprint', 'recycled content', 'sustainable material', 'eco-label', 'environmental certification', 'energy label', 'net zero'],
        'extraction_strategy': 'llm_with_context'
    },
    {
        'name': 'blast_resistance',
        'display_name': 'Blast / Explosion Resistance',
        'description': 'Blast resistance or explosion protection requirement for the facade including peak reflected overpressure, positive phase duration, glazing hazard level, and referenced standard (ISO 16933, EN 13541, GSA, UFC)',
        'expected_units': ['kPa', 'psi', 'hazard level', 'ms'],
        'value_type': 'text',
        'search_keywords': ['blast resistance', 'blast load', 'explosion resistance', 'blast protection', 'overpressure', 'peak pressure', 'glazing hazard', 'ISO 16933', 'EN 13541', 'GSA glazing', 'UFC 4-010', 'anti-blast', 'security glazing', 'bomb blast', 'blast proof'],
        'extraction_strategy': 'llm_with_context'
    },
]
