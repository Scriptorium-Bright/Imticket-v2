package org.example.ticket.reservation.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.member.domain.Member;

import java.time.LocalDateTime;

@Entity
@Table(name = "reservation_idempotency",
        uniqueConstraints = @UniqueConstraint(name = "uk_reservation_idempotency_member_key",
                columnNames = {"member_id", "idempotency_key"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class ReservationIdempotency {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "member_id", nullable = false)
    private Member member;
    @Column(name = "idempotency_key", nullable = false, length = 120)
    private String idempotencyKey;
    @Column(name = "request_hash", nullable = false, length = 64)
    private String requestHash;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private IdempotencyStatus status;
    @Column(name = "attempt_token", nullable = false, length = 36)
    private String attemptToken;
    @Column(name = "lease_expires_at", nullable = false)
    private LocalDateTime leaseExpiresAt;
    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "reservation_id")
    private Reservation reservation;
    @Column(name = "last_error_code", length = 80)
    private String lastErrorCode;

    public boolean ownedBy(Long memberId, String hash, String token) {
        return status == IdempotencyStatus.PROCESSING
                && member.getId().equals(memberId)
                && requestHash.equals(hash)
                && attemptToken.equals(token);
    }

    public void reclaim(String token, LocalDateTime leaseUntil) {
        status = IdempotencyStatus.PROCESSING;
        attemptToken = token;
        leaseExpiresAt = leaseUntil;
        lastErrorCode = null;
    }

    public void succeed(Reservation reservation) {
        status = IdempotencyStatus.SUCCEEDED;
        this.reservation = reservation;
        leaseExpiresAt = LocalDateTime.MIN;
    }

    public void fail(String errorCode) {
        status = IdempotencyStatus.FAILED;
        lastErrorCode = errorCode;
        leaseExpiresAt = LocalDateTime.MIN;
    }
}
