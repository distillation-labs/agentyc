DISABLED_ELEMENTS = {'style', 'script', 'head', 'meta', 'link', 'title'}

SVG_ELEMENTS = {
	'path',
	'rect',
	'g',
	'circle',
	'ellipse',
	'line',
	'polyline',
	'polygon',
	'use',
	'defs',
	'clipPath',
	'mask',
	'pattern',
	'image',
	'text',
	'tspan',
}


PROPAGATING_ELEMENTS = [
	{'tag': 'a', 'role': None},
	{'tag': 'button', 'role': None},
	{'tag': 'div', 'role': 'button'},
	{'tag': 'div', 'role': 'combobox'},
	{'tag': 'span', 'role': 'button'},
	{'tag': 'span', 'role': 'combobox'},
	{'tag': 'input', 'role': 'combobox'},
	{'tag': 'input', 'role': 'combobox'},
]


DEFAULT_CONTAINMENT_THRESHOLD = 0.99
