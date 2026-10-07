package org.example.ticket.payment.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.payment.domain.*;
import org.example.ticket.payment.dto.*;
import org.example.ticket.payment.repository.*;
import org.example.ticket.reservation.domain.*;
import org.example.ticket.reservation.repository.*;
import org.example.ticket.seat.domain.Seat;
import org.example.ticket.seat.repository.SeatRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class PaymentCompletionService {
    private final PaymentOrderRepository orderRepository;
    private final PaymentAttemptRepository attemptRepository;
    private final ReservationRepository reservationRepository;
    private final ReservedSeatRepository reservedSeatRepository;
    private final SeatRepository seatRepository;

    @Transactional
    public PaymentVerificationResponse complete(Long memberId, Long paymentOrderId, VerifiedPayment verified) {
        Long reservationId = orderRepository.findReservationIdById(paymentOrderId)
                .orElseThrow(() -> notFound("PAYMENT_ORDER_NOT_FOUND", "결제 주문을 찾을 수 없습니다."));
        Reservation reservation = reservationRepository.findByIdForUpdate(reservationId)
                .orElseThrow(() -> notFound("RESERVATION_NOT_FOUND", "예약을 찾을 수 없습니다."));
        List<Long> seatIds = reservedSeatRepository.findSeatIdsByReservationIds(List.of(reservationId));
        List<Seat> seats = seatIds.isEmpty() ? List.of() : seatRepository.findByIdsForUpdate(seatIds);
        PaymentOrder order = orderRepository.findByIdForUpdate(paymentOrderId)
                .orElseThrow(() -> notFound("PAYMENT_ORDER_NOT_FOUND", "결제 주문을 찾을 수 없습니다."));

        if (!order.getMember().getId().equals(memberId)) {
            throw new BusinessException(HttpStatus.FORBIDDEN, "PAYMENT_NOT_OWNER", "본인의 결제만 확인할 수 있습니다.");
        }
        if (order.getStatus() == PaymentOrderStatus.APPLIED
                || order.getStatus() == PaymentOrderStatus.REFUND_PENDING
                || order.getStatus() == PaymentOrderStatus.REFUNDED) {
            return PaymentVerificationResponse.of(order, reservation, verified.providerTransactionId());
        }
        validate(order, verified);

        PaymentAttempt attempt = attemptRepository.findTopByPaymentOrderIdOrderByIdDesc(order.getId())
                .orElseThrow(() -> notFound("PAYMENT_ATTEMPT_NOT_FOUND", "결제 시도를 찾을 수 없습니다."));
        attempt.verify(verified.providerTransactionId(), verified.approvedAt());
        order.markPaidUnapplied();

        boolean expired = reservation.getStatus() == ReservationStatus.EXPIRED
                || (reservation.getStatus() == ReservationStatus.PENDING_PAYMENT
                    && reservation.getExpiredAt() != null
                    && reservation.getExpiredAt().isBefore(LocalDateTime.now()));

        if (expired) {
            if (reservation.getStatus() == ReservationStatus.PENDING_PAYMENT) {
                reservation.expire();
                seats.forEach(Seat::release);
            }
            order.requestRefund();
        } else {
            reservation.confirm();
            seats.forEach(Seat::reserve);
            order.apply();
        }
        return PaymentVerificationResponse.of(order, reservation, verified.providerTransactionId());
    }

    private void validate(PaymentOrder order, VerifiedPayment verified) {
        if (!order.getMerchantOrderId().equals(verified.merchantOrderId())
                || !order.getAmount().equals(verified.amount())
                || !order.getCurrency().equals(verified.currency())) {
            throw new BusinessException(HttpStatus.CONFLICT, "PAYMENT_DETAILS_MISMATCH", "결제 승인 정보가 주문과 일치하지 않습니다.");
        }
    }

    private BusinessException notFound(String code, String message) {
        return new BusinessException(HttpStatus.NOT_FOUND, code, message);
    }
}
