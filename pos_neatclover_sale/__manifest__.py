# -*- coding: utf-8 -*-
# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
{
    'name': 'Clover PoS Sale/Invoice Payments',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Clover Sale Order & Invoice Terminal Payments',
    'description': 'Pay sale orders and invoices with Clover POS terminals - depends on PoS Terminal Payment Integration Clover',
    'website': 'https://www.sns-software.com',
    'author': 'SNS Software LTD',
    'maintainer': 'SNS Software LTD',
    'depends': ['pos_neatclover', 'sale', 'account', 'payment', 'account_payment'],
    'data': [
        'security/ir.model.access.csv',
        'data/payment_provider_data.xml',
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'views/clover_terminal_payment_templates.xml',
        'wizard/clover_terminal_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pos_neatclover_sale/static/src/css/clover_terminal_wizard.css',
        ],
    },
    'images': ['static/description/main.gif'],
    'installable': True,
    'application': False,
    'auto_install': True,
    'license': 'LGPL-3',
}
