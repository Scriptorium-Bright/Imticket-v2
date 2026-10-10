package org.example.ticket.reservation.dto;

import org.example.ticket.reservation.domain.Reservation;
import org.example.ticket.reservation.domain.ReservationStatus;
import org.example.ticket.seat.dto.SeatResponse;

import java.time.LocalDateTime;
import java.util.List;

public record ReservationDetailResponse(
        Long id,
        String reservationCode,
        Integer totalPrice,
        ReservationStatus status,
        LocalDateTime expiredAt,
        List<SeatResponse> seats
) {
    public static ReservationDetailResponse of(Reservation reservation, List<SeatResponse> seats) {
        return new ReservationDetailResponse(
                reservation.getId(),
                reservation.getReservationCode(),
                reservation.getTotalPrice(),
                reservation.getStatus(),
                reservation.getExpiredAt(),
                List.copyOf(seats)
        );
    }
}
