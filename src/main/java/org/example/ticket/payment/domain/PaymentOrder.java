package org.example.ticket.payment.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.member.domain.Member;
import org.example.ticket.reservation.domain.Reservation;

@Entity
@Table(name = "payment_order",
        uniqueConstraints = {
                @UniqueConstraint(name = "uk_payment_member_idempotency", columnNames = {"member_id", "idempotency_key"}),
                @UniqueConstraint(name = "uk_payment_merchant_order", columnNames = "merchant_order_id"),
                @UniqueConstraint(name = "uk_payment_reservation", columnNames = "reservation_id")
        })
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class PaymentOrder {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @OneToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "reservation_id", nullable = false)
    private Reservation reservation;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "member_id", nullable = false)
    private Member member;
    @Column(name = "merchant_order_id", nullable = false, length = 100)
    private String merchantOrderId;
    @Column(nullable = false)
    private Integer amount;
    @Column(nullable = false, length = 3)
    private String currency;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 30)
    private PaymentOrderStatus status;
    @Column(name = "idempotency_key", nullable = false, length = 120)
    private String idempotencyKey;
    @Column(name = "request_hash", nullable = false, length = 64)
    private String requestHash;

    public void markPaidUnapplied() { status = PaymentOrderStatus.PAID_UNAPPLIED; }
    public void apply() { status = PaymentOrderStatus.APPLIED; }
    public void requestRefund() { status = PaymentOrderStatus.REFUND_PENDING; }
    public void refund() { status = PaymentOrderStatus.REFUNDED; }
}
