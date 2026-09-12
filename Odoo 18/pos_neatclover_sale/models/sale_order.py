# -*- coding: utf-8 -*-
from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_open_clover_terminal_payment_wizard(self):
        order = self.env['clover.document.payment'].ensure_single_document(
            self, _('sales order'),
        )
        self.env['clover.document.payment'].assert_document_not_fully_paid_for_payment(
            sale_order=order,
        )
        return {
            'name': _('Pay by Clover Terminal'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.terminal.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                **self.env.context,
                'default_sale_order_id': order.id,
                'dialog_size': 'medium',
            },
        }

    def action_open_clover_terminal_refund_wizard(self):
        order = self.env['clover.document.payment'].ensure_single_document(
            self, _('sales order'),
        )
        self.env['clover.document.payment'].assert_document_fully_paid_for_refund(sale_order=order)
        return {
            'name': _('Refund by Clover Terminal'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.terminal.refund.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                **self.env.context,
                'default_sale_order_id': order.id,
                'dialog_size': 'medium',
            },
        }
