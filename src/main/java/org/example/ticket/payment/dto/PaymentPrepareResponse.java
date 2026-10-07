package org.example.ticket.payment.dto;
import org.example.ticket.payment.domain.PaymentOrder;
public record PaymentPrepareResponse(Long paymentOrderId, String merchantOrderId, Integer amount,
                                     String currency, String provider, String providerPaymentId) {
    public static PaymentPrepareResponse of(PaymentOrder order, String provider, String providerPaymentId) {
        return new PaymentPrepareResponse(order.getId(), order.getMerchantOrderId(), order.getAmount(),
                order.getCurrency(), provider, providerPaymentId);
    }
}
