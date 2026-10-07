package org.example.ticket.reservation.dto;

import org.example.ticket.reservation.domain.Reservation;
import org.example.ticket.reservation.domain.ReservationStatus;

import java.time.LocalDateTime;

public record ReservationResponse(
        Long id,
        String reservationCode,
        Integer totalPrice,
        ReservationStatus status,
        LocalDateTime expiredAt
) {
    public static ReservationResponse from(Reservation reservation) {
        return new ReservationResponse(
                reservation.getId(),
                reservation.getReservationCode(),
                reservation.getTotalPrice(),
                reservation.getStatus(),
                reservation.getExpiredAt()
        );
    }
}
