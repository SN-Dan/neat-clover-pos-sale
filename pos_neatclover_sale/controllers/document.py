# -*- coding: utf-8 -*-
import logging
from urllib.parse import quote

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CloverDocumentController(http.Controller):

    _WIZARD_MODELS = {
        'payment': 'clover.terminal.payment.wizard',
        'preauth': 'clover.terminal.payment.wizard',
        'refund': 'clover.terminal.refund.wizard',
        'charge': 'clover.terminal.charge.wizard',
        'void': 'clover.terminal.charge.wizard',
    }

    def _get_processing_wizard(self, wizard_id, wizard_type=None):
        if wizard_type in self._WIZARD_MODELS:
            wizard = request.env[self._WIZARD_MODELS[wizard_type]].browse(wizard_id).exists()
            if wizard and wizard.state == 'processing' and wizard.transaction_id:
                return wizard, wizard_type
            return request.env['clover.terminal.payment.wizard'].browse(), wizard_type

        for wtype, model_name in self._WIZARD_MODELS.items():
            wizard = request.env[model_name].browse(wizard_id).exists()
            if wizard and wizard.state == 'processing' and wizard.transaction_id:
                return wizard, wtype
        return request.env['clover.terminal.payment.wizard'].browse(), wizard_type

    def _document_helpers(self):
        return request.env['clover.document.payment']

    def _get_payment_request(self, terminal_id, transaction_id):
        return request.env['neat.clover.payment.request'].sudo().search([
            ('transaction_id', '=', transaction_id),
            ('terminal_id', '=', terminal_id),
        ], limit=1)

    @http.route([
        '/pos_clover/terminal_payment_page/<string:wizard_type>/<int:wizard_id>',
        '/pos_clover/terminal_payment_page/<int:wizard_id>',
    ], type='http', auth='user')
    def terminal_payment_page(self, wizard_id, wizard_type=None, **kwargs):
        wizard, wizard_type = self._get_processing_wizard(wizard_id, wizard_type)
        if not wizard:
            return request.not_found()
        payment_method = wizard.payment_method_id
        is_refund = wizard_type == 'refund'
        operation = wizard_type or 'payment'
        if wizard_type in ('payment', 'preauth') and getattr(wizard, 'operation', False):
            operation = wizard.operation
        elif wizard_type in ('charge', 'void') and getattr(wizard, 'operation', False):
            operation = 'void' if wizard.operation == 'void' else 'charge'
        return request.render('pos_neatclover_sale.terminal_payment_page', {
            'wizard_id': wizard_id,
            'wizard_type': wizard_type,
            'is_refund': is_refund,
            'operation': operation,
            'terminal_id': payment_method.neat_clover_terminal_device_code,
            'transaction_id': wizard.transaction_id,
            'payment_method_id': payment_method.id,
            'is_desktop_mode': bool(payment_method.neat_clover_is_desktop_mode),
            'is_local_ws_server': bool(payment_method.neat_clover_is_local_ws_server),
            'is_mobile': bool(payment_method.neat_clover_is_mobile),
            'ws_url': quote(payment_method.neat_clover_ws_url or '', safe=''),
        })

    @http.route('/pos_clover/document_payment_status', type='json', auth='user', methods=['POST'])
    def document_payment_status(self, terminal_id, transaction_id, payment_method_id=None):
        payment_request = self._get_payment_request(terminal_id, transaction_id)
        if not payment_request:
            return {'status': 404}

        helpers = self._document_helpers()
        if helpers.is_quotation_or_invoice_request(payment_request) and not payment_request.status.startswith('processed_'):
            payment_method = request.env['pos.payment.method'].browse(payment_method_id).exists()
            if not payment_method:
                payment_method = helpers._payment_method_for_terminal(terminal_id)
            helpers.finalize_document_payment_request(payment_request, payment_method)

        status = helpers.get_document_payment_status(payment_request)
        return {'status': 200, 'data': status}

    @http.route('/pos_clover/document_finalize', type='json', auth='user', methods=['POST'])
    def document_finalize(self, terminal_id, transaction_id, payment_method_id):
        payment_request = self._get_payment_request(terminal_id, transaction_id)
        if not payment_request:
            return {'status': 404}

        payment_method = request.env['pos.payment.method'].browse(payment_method_id).exists()
        if not payment_method:
            payment_method = self._document_helpers()._payment_method_for_terminal(terminal_id)

        if not self._document_helpers().finalize_document_payment_request(payment_request, payment_method):
            return {'status': 400}

        return {'status': 200}

    @http.route('/pos_clover/terminal_wizard_done', type='json', auth='user', methods=['POST'])
    def terminal_wizard_done(self, wizard_id, transaction_id, wizard_type=None):
        wizard, _wizard_type = self._get_processing_wizard(wizard_id, wizard_type)
        if not wizard:
            for model_name in self._WIZARD_MODELS.values():
                candidate = request.env[model_name].browse(wizard_id).exists()
                if candidate and candidate.transaction_id == transaction_id:
                    wizard = candidate
                    break
        if wizard and wizard.transaction_id == transaction_id:
            wizard.write({'state': 'done'})
        return {'status': 200}
