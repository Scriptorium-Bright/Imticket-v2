package org.example.ticket.payment.dto;
import org.example.ticket.payment.domain.PaymentOrder;
import org.example.ticket.payment.domain.PaymentOrderStatus;
import org.example.ticket.reservation.domain.Reservation;
import org.example.ticket.reservation.domain.ReservationStatus;
public record PaymentVerificationResponse(Long paymentOrderId, PaymentOrderStatus paymentStatus,
                                          Long reservationId, ReservationStatus reservationStatus,
                                          String providerTransactionId) {
    public static PaymentVerificationResponse of(PaymentOrder order, Reservation reservation, String transactionId) {
        return new PaymentVerificationResponse(order.getId(), order.getStatus(), reservation.getId(),
                reservation.getStatus(), transactionId);
    }
}
