package org.example.ticket.venue.dto;
import jakarta.validation.constraints.*;
import org.example.ticket.venue.domain.SeatGrade;
public record CreateVenueHallRequest(@NotBlank @Size(max=120) String name,
                                     @NotBlank @Size(max=20) String section,
                                     @Min(1) @Max(1000) int rows,
                                     @Min(1) @Max(1000) int seatsPerRow,
                                     @NotNull SeatGrade grade) {}
