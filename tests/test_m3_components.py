import unittest

from jinja2 import Environment, FileSystemLoader


class TestM3Components(unittest.TestCase):
    def setUp(self):
        self.env = Environment(loader=FileSystemLoader("templates"))
        self.env.globals["_"] = lambda s: s
        self.env.filters["pic"] = lambda s: s
        self.env.filters["date"] = lambda s: s
        self.env.filters["intsep"] = lambda s: str(s)

    def test_m3_button_macro_renders(self):
        template = self.env.from_string("""
            {% from 'themes/modern/components/m3_components.html' import m3_button %}
            {{ m3_button('Click me', variant='filled') }}
        """)
        rendered = template.render()
        self.assertIn("md3-button", rendered)
        self.assertIn("md3-button-filled", rendered)
        self.assertIn("Click me", rendered)

    def test_m3_card_macro_renders_elevated_style(self):
        template = self.env.from_string("""
            {% from 'themes/modern/components/m3_components.html' import m3_card %}
            {{ m3_card('Test Title', '/video/BV1', 'pic.jpg', duration='10:00', author='Test Author', author_id='123', views=1000) }}
        """)
        rendered = template.render()
        self.assertIn("rounded-2xl", rendered)
        self.assertIn("Test Title", rendered)
        self.assertIn("Test Author", rendered)
        self.assertIn("bg-surface-container-low", rendered)

    def test_m3_popover_renders_native_popover_attribute(self):
        template = self.env.from_string("""
            {% from 'themes/modern/components/m3_components.html' import m3_popover %}
            {% call m3_popover('menu_id') %}<div>Menu Content</div>{% endcall %}
        """)
        rendered = template.render()
        self.assertIn('popover="auto"', rendered)
        self.assertIn('id="menu_id"', rendered)

    def test_m3_chip_macro_renders_with_title(self):
        template = self.env.from_string("""
            {% from 'themes/modern/components/m3_components.html' import m3_chip %}
            {{ m3_chip(label='1,234', icon='ion-ios-thumbs-up', title='Likes') }}
        """)
        rendered = template.render()
        self.assertIn('1,234', rendered)
        self.assertIn('title="Likes"', rendered)
        self.assertIn('ion-ios-thumbs-up', rendered)

