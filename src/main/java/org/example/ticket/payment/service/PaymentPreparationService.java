package org.example.ticket.payment.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.member.domain.Member;
import org.example.ticket.member.repository.MemberRepository;
import org.example.ticket.payment.domain.*;
import org.example.ticket.payment.dto.*;
import org.example.ticket.payment.gateway.PaymentGatewayClient;
import org.example.ticket.payment.repository.*;
import org.example.ticket.reservation.domain.*;
import org.example.ticket.reservation.repository.ReservationRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class PaymentPreparationService {
    private final MemberRepository memberRepository;
    private final ReservationRepository reservationRepository;
    private final PaymentOrderRepository orderRepository;
    private final PaymentAttemptRepository attemptRepository;
    private final PaymentGatewayClient gateway;

    @Transactional
    public PaymentPrepareResponse prepare(Long memberId, String idempotencyKey, PaymentPrepareRequest request) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw error(HttpStatus.BAD_REQUEST, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key가 필요합니다.");
        }
        String hash = sha256(request.reservationId().toString());
        var existing = orderRepository.findByMemberIdAndIdempotencyKey(memberId, idempotencyKey.trim());
        if (existing.isPresent()) {
            PaymentOrder order = existing.get();
            if (!order.getRequestHash().equals(hash)) {
                throw error(HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT", "같은 멱등 키에 다른 요청을 사용할 수 없습니다.");
            }
            return response(order);
        }

        Member member = memberRepository.findById(memberId)
                .orElseThrow(() -> error(HttpStatus.UNAUTHORIZED, "MEMBER_NOT_FOUND", "사용자를 찾을 수 없습니다."));
        Reservation reservation = reservationRepository.findByIdForUpdate(request.reservationId())
                .orElseThrow(() -> error(HttpStatus.NOT_FOUND, "RESERVATION_NOT_FOUND", "예약을 찾을 수 없습니다."));
        if (!reservation.getMember().getId().equals(memberId)) {
            throw error(HttpStatus.FORBIDDEN, "RESERVATION_NOT_OWNER", "본인의 예약만 결제할 수 있습니다.");
        }
        if (reservation.getStatus() != ReservationStatus.PENDING_PAYMENT) {
            throw error(HttpStatus.CONFLICT, "RESERVATION_NOT_PAYABLE", "결제 가능한 예약 상태가 아닙니다.");
        }

        PaymentOrder order = orderRepository.save(PaymentOrder.builder()
                .reservation(reservation).member(member)
                .merchantOrderId("imt-" + reservation.getReservationCode() + "-" + UUID.randomUUID())
                .amount(reservation.getTotalPrice()).currency("KRW").status(PaymentOrderStatus.READY)
                .idempotencyKey(idempotencyKey.trim()).requestHash(hash).build());
        attemptRepository.save(PaymentAttempt.builder()
                .paymentOrder(order).provider(gateway.provider()).status(PaymentAttemptStatus.READY).build());
        return response(order);
    }

    private PaymentPrepareResponse response(PaymentOrder order) {
        PaymentAuthorization authorization = new PaymentAuthorization(order.getId(), order.getReservation().getId(),
                order.getMerchantOrderId(), order.getAmount(), order.getCurrency());
        return PaymentPrepareResponse.of(order, gateway.provider(), gateway.providerPaymentId(authorization));
    }

    private String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private BusinessException error(HttpStatus status, String code, String message) {
        return new BusinessException(status, code, message);
    }
}
