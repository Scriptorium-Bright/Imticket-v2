package org.example.ticket.reservation.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.seat.domain.Seat;

@Entity
@Table(name = "reserved_seat",
        uniqueConstraints = @UniqueConstraint(name = "uk_reserved_seat_reservation_seat",
                columnNames = {"reservation_id", "seat_id"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class ReservedSeat {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "reservation_id", nullable = false)
    private Reservation reservation;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "seat_id", nullable = false)
    private Seat seat;
}
