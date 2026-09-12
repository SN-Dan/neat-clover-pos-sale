# -*- coding: utf-8 -*-
from odoo import _, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_open_clover_terminal_payment_wizard(self):
        invoice = self.env['clover.document.payment'].ensure_single_document(
            self, _('invoice'),
        )
        self.env['clover.document.payment'].assert_document_not_fully_paid_for_payment(
            invoice=invoice,
        )
        return {
            'name': _('Pay by Clover Terminal'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.terminal.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                **self.env.context,
                'default_invoice_id': invoice.id,
                'dialog_size': 'medium',
            },
        }

    def action_open_clover_terminal_refund_wizard(self):
        invoice = self.env['clover.document.payment'].ensure_single_document(
            self, _('invoice'),
        )
        self.env['clover.document.payment'].assert_document_fully_paid_for_refund(invoice=invoice)
        return {
            'name': _('Refund by Clover Terminal'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.terminal.refund.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                **self.env.context,
                'default_invoice_id': invoice.id,
                'dialog_size': 'medium',
            },
        }
