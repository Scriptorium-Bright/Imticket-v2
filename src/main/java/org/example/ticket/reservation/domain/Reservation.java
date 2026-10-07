package org.example.ticket.reservation.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.member.domain.Member;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "reservation",
        indexes = @Index(name = "idx_reservation_status_expired_at", columnList = "reservation_status, expired_at"))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class Reservation {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "reservation_code", nullable = false, unique = true, length = 36)
    private String reservationCode;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "member_id", nullable = false)
    private Member member;
    @Column(name = "total_price", nullable = false)
    private Integer totalPrice;
    @Enumerated(EnumType.STRING)
    @Column(name = "reservation_status", nullable = false, length = 30)
    private ReservationStatus status;
    @Column(name = "expired_at")
    private LocalDateTime expiredAt;
    @Builder.Default
    @OneToMany(mappedBy = "reservation", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<ReservedSeat> reservedSeats = new ArrayList<>();

    public void setReservedSeats(List<ReservedSeat> seats) { this.reservedSeats = new ArrayList<>(seats); }
    public void confirm() { status = ReservationStatus.CONFIRMED; expiredAt = null; }
    public void expire() { status = ReservationStatus.EXPIRED; }
}
