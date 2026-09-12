# -*- coding: utf-8 -*-
from odoo import fields, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('neatcloverpos', "Clover Sales POS")],
        ondelete={'neatcloverpos': 'set default'},
    )
