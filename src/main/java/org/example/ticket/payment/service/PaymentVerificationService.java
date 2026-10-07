package org.example.ticket.payment.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.payment.domain.PaymentOrder;
import org.example.ticket.payment.dto.*;
import org.example.ticket.payment.gateway.PaymentGatewayClient;
import org.example.ticket.payment.repository.PaymentOrderRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class PaymentVerificationService {
    private final PaymentOrderRepository orderRepository;
    private final PaymentGatewayClient gateway;
    private final PaymentCompletionService completionService;

    public PaymentVerificationResponse verify(Long memberId, Long paymentOrderId, PaymentVerifyRequest request) {
        PaymentOrder order = orderRepository.findByIdWithOwner(paymentOrderId)
                .orElseThrow(() -> new BusinessException(HttpStatus.NOT_FOUND,
                        "PAYMENT_ORDER_NOT_FOUND", "결제 주문을 찾을 수 없습니다."));
        if (!order.getMember().getId().equals(memberId)) {
            throw new BusinessException(HttpStatus.FORBIDDEN, "PAYMENT_NOT_OWNER", "본인의 결제만 확인할 수 있습니다.");
        }

        PaymentAuthorization authorization = new PaymentAuthorization(
                order.getId(), order.getReservation().getId(), order.getMerchantOrderId(),
                order.getAmount(), order.getCurrency()
        );
        VerifiedPayment verified = gateway.verify(authorization, request.providerPaymentId());
        return completionService.complete(memberId, paymentOrderId, verified);
    }
}
